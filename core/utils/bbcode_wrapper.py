import bbcode
import re

# Initialize the parser globally or on demand
_PARSER = None

def _get_parser():
    global _PARSER
    if _PARSER:
        return _PARSER

    # Create parser with escape_html=False to prevent double-escaping if we handle sanitization
    # However, for general use, we might want default escaping. 
    # The existing server logic used escape_html=False.
    parser = bbcode.Parser(escape_html=False, replace_cosmetic=False, replace_links=False)

    # --- Custom Formatters ---

    def format_size(tag_name, value, options, parent, context):
        size_map = {
            "1": "10px", "2": "13px", "3": "16px", 
            "4": "18px", "5": "24px", "6": "32px", "7": "48px"
        }
        size_attr = options.get('size', value)
        font_size = size_map.get(str(size_attr), "16px")
        # If size looks like a valid css unit (e.g. 20px), use it
        if str(size_attr).endswith("px") or str(size_attr).endswith("em"):
            font_size = size_attr
        return f'<span style="font-size:{font_size}">{value}</span>'

    def format_font(tag_name, value, options, parent, context):
        font_family = options.get('font', 'inherit')
        # Basic sanitization
        if any(c in font_family for c in "<>\"'"):
            font_family = "inherit"
        return f'<span style="font-family:{font_family}">{value}</span>'

    def format_align(tag_name, value, options, parent, context):
        # tag_name is 'left', 'center', 'right', or 'justify'
        return f'<div style="text-align:{tag_name}">{value}</div>'

    def format_color(tag_name, value, options, parent, context):
        color = options.get('color', 'black')
        # Basic sanitization
        if any(c in color for c in "<>\""):
            color = "black"
        return f'<span style="color:{color}">{value}</span>'

    def format_img(tag_name, value, options, parent, context):
        return f'<img src="{value}" style="max-width:100%;height:auto;display:inline-block" alt="" />'

    def format_sub(tag_name, value, options, parent, context):
        return f'<sub>{value}</sub>'

    def format_sup(tag_name, value, options, parent, context):
        return f'<sup>{value}</sup>'

    def format_hr(tag_name, value, options, parent, context):
        return '<hr />'

    def format_spoiler(tag_name, value, options, parent, context):
        title = options.get('spoiler', 'Spoiler')
        if not title:
            title = "Spoiler"
        return f'<details><summary>{title}</summary><div style="padding:10px;border:1px solid #444;background:#222;">{value}</div></details>'

    def format_youtube(tag_name, value, options, parent, context):
        vid_id = value.strip().replace('"', "").replace("'", "")
        if not re.match(r'^[a-zA-Z0-9_-]+$', vid_id):
            return value
        return f'<div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;max-width:100%;"><iframe style="position:absolute;top:0;left:0;width:100%;height:100%;" src="https://www.youtube.com/embed/{vid_id}" frameborder="0" allowfullscreen></iframe></div>'

    def format_email(tag_name, value, options, parent, context):
        addr = options.get('email', value)
        return f'<a href="mailto:{addr}">{value}</a>'

    # Register all formatters
    parser.add_formatter('size', format_size)
    parser.add_formatter('font', format_font)
    parser.add_formatter('left', format_align)
    parser.add_formatter('center', format_align)
    parser.add_formatter('right', format_align)
    parser.add_formatter('justify', format_align)
    parser.add_formatter('color', format_color)
    parser.add_formatter('img', format_img, replace_links=False, replace_cosmetic=False, render_embedded=False)
    parser.add_formatter('sub', format_sub)
    parser.add_formatter('sup', format_sup)
    parser.add_formatter('hr', format_hr, standalone=True)
    parser.add_formatter('spoiler', format_spoiler)
    parser.add_formatter('youtube', format_youtube)
    parser.add_formatter('email', format_email)

    # Disable HTML escaping for ALL tags (builtin and custom) so that things like <br /> are passed through.
    # The frontend uses DOMPurify to sanitize the resulting HTML.
    for tag_name, (render_func, tag_options) in parser.recognized_tags.items():
        tag_options.escape_html = False

    _PARSER = parser
    return _PARSER

def bbcode_to_html(text: str) -> str:
    """Convert BBCode text to HTML using the centralized parser logic."""
    if not text:
        return ""
    parser = _get_parser()
    return parser.format(text)
