#include <iostream>
#include <fstream>
#include <vector>
#include <string>
#include <cstdint>
#include <stdexcept>
#include <cstring>
#include <algorithm>

enum class LocresVersion : uint8_t {
    Legacy = 0,
    Compact = 1,
    Optimized = 2,
    CityHash = 3
};

const uint8_t LOCRES_MAGIC[] = {
    0x0e, 0x14, 0x74, 0x75, 0x67, 0x4a, 0x03, 0xfc,
    0x4a, 0x15, 0x90, 0x9d, 0xc3, 0x37, 0x7f, 0x1b
};

class Reader {
    std::ifstream file;
public:
    Reader(const std::string& path) {
        file.open(path, std::ios::binary);
        if (!file.is_open()) {
            throw std::runtime_error("Cannot open file");
        }
    }
    
    void ReadBytes(void* dest, size_t size) {
        file.read(reinterpret_cast<char*>(dest), size);
    }
    
    uint8_t ReadUInt8() { uint8_t v; ReadBytes(&v, 1); return v; }
    int32_t ReadInt32() { int32_t v; ReadBytes(&v, 4); return v; }
    uint32_t ReadUInt32() { uint32_t v; ReadBytes(&v, 4); return v; }
    uint64_t ReadUInt64() { uint64_t v; ReadBytes(&v, 8); return v; }
    
    void SetPos(uint64_t pos) {
        file.seekg(pos, std::ios::beg);
    }
    
    std::string ReadString() {
        int32_t length = ReadInt32();
        if (length == 0) return "";
        if (length > 0) {
            std::string s(length - 1, '\0');
            ReadBytes(&s[0], length - 1);
            ReadUInt8(); // null terminator
            return s;
        } else {
            length = -length;
            std::vector<char16_t> s16(length - 1);
            ReadBytes(s16.data(), (length - 1) * 2);
            ReadBytes(nullptr, 2); // null terminator (read and discard by seeking or reading to dummy)
            // Wait, we need to read 2 bytes, ReadBytes(nullptr) will crash!
            char16_t nullTerm;
            ReadBytes(&nullTerm, 2);
            
            // Convert to UTF-8
            std::string utf8;
            for (char16_t c : s16) {
                if (c < 0x80) {
                    utf8 += static_cast<char>(c);
                } else if (c < 0x800) {
                    utf8 += static_cast<char>((c >> 6) | 0xC0);
                    utf8 += static_cast<char>((c & 0x3F) | 0x80);
                } else {
                    utf8 += static_cast<char>((c >> 12) | 0xE0);
                    utf8 += static_cast<char>(((c >> 6) & 0x3F) | 0x80);
                    utf8 += static_cast<char>((c & 0x3F) | 0x80);
                }
            }
            return utf8;
        }
    }
};

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "Usage: fast_locres <file.locres>" << std::endl;
        return 1;
    }
    
    try {
        Reader r(argv[1]);
        uint8_t magic[16];
        r.ReadBytes(magic, 16);
        if (std::memcmp(magic, LOCRES_MAGIC, 16) != 0) {
            std::cerr << "Invalid magic" << std::endl;
            return 1;
        }
        
        LocresVersion version = static_cast<LocresVersion>(r.ReadUInt8());
        uint64_t offset = r.ReadUInt64();
        
        std::vector<std::string> strings;
        if (version >= LocresVersion::Compact) {
            r.SetPos(offset);
            uint32_t string_count = r.ReadUInt32();
            for (uint32_t i = 0; i < string_count; ++i) {
                strings.push_back(r.ReadString());
                if (version >= LocresVersion::Optimized) {
                    r.ReadUInt32(); // ref count
                }
            }
        }
        
        if (version == LocresVersion::Legacy) r.SetPos(0);
        if (version >= LocresVersion::Compact) r.SetPos(25);
        if (version >= LocresVersion::Optimized) r.ReadUInt32(); // entrys_count
        
        uint32_t namespace_count = r.ReadUInt32();
        for (uint32_t i = 0; i < namespace_count; ++i) {
            if (version >= LocresVersion::Optimized) r.ReadUInt32(); // namespace_key_hash
            
            std::string ns_name = r.ReadString();
            uint32_t key_count = r.ReadUInt32();
            
            for (uint32_t j = 0; j < key_count; ++j) {
                if (version >= LocresVersion::Optimized) r.ReadUInt32(); // string_key_hash
                
                std::string string_key = r.ReadString();
                r.ReadUInt32(); // source_string_hash
                
                std::string translation;
                if (version >= LocresVersion::Compact) {
                    uint32_t string_index = r.ReadUInt32();
                    translation = strings[string_index];
                } else {
                    translation = r.ReadString();
                }
                
                // Filter: we only care about keys containing "HeroUI", "ItemTable", "UISkin", "HeroBasic", "123_Customize"
                // To be safe, let's output everything and filter in Python? No, that defeats the purpose of C++ filtering!
                // Actually, python parsing of line-by-line is fast enough. But doing minimal filtering here is better.
                if (ns_name.find("123_Customize_") == 0 || ns_name.find("601_HeroUIAsset_") == 0 ||
                    string_key.find("UIHeroTable_") != std::string::npos ||
                    string_key.find("MarvelItemTable_") != std::string::npos ||
                    string_key.find("UISkinTable_") != std::string::npos ||
                    string_key.find("HeroUIAssetBPTable_") != std::string::npos) {
                    
                    // Output format: ns_name|string_key|translation
                    // Replace newlines with \n literal for flat output
                    std::string flat_trans = translation;
                    size_t pos = 0;
                    while((pos = flat_trans.find("\n", pos)) != std::string::npos) {
                        flat_trans.replace(pos, 1, "\\n");
                        pos += 2;
                    }
                    while((pos = flat_trans.find("\r", pos)) != std::string::npos) {
                        flat_trans.replace(pos, 1, "\\r");
                        pos += 2;
                    }
                    
                    std::cout << ns_name << "|" << string_key << "|" << flat_trans << "\n";
                }
            }
        }
        
    } catch(std::exception& e) {
        std::cerr << e.what() << std::endl;
        return 1;
    }
    return 0;
}
