import logging
import io

class EndpointFilter(logging.Filter):
    def __init__(self, start_path: str):
        super().__init__()
        self.start_path = start_path
        self.has_logged = False

    def filter(self, record: logging.LogRecord) -> bool:
        # If the message contains our target path
        if self.start_path in record.getMessage():
            if not self.has_logged:
                self.has_logged = True
                return True
            return False
        return True

def test_endpoint_filter():
    # Setup
    logger = logging.getLogger("test_logger")
    logger.setLevel(logging.INFO)
    
    # Capture output
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    
    # Add filter
    filt = EndpointFilter("GET /api/nxm/handoffs")
    logger.addFilter(filt)
    
    # Test
    logger.info("This is a random log")
    logger.info("INFO:     127.0.0.1:62976 - \"GET /api/nxm/handoffs HTTP/1.1\" 200 OK")
    logger.info("Another random log")
    logger.info("INFO:     127.0.0.1:62976 - \"GET /api/nxm/handoffs HTTP/1.1\" 200 OK")
    logger.info("Final random log")
    
    # Verify
    output = stream.getvalue()
    print("captured output:\n", output)
    
    lines = output.strip().split('\n')
    assert len(lines) == 4, f"Expected 4 lines, got {len(lines)}"
    assert "This is a random log" in lines[0]
    assert "GET /api/nxm/handoffs" in lines[1]
    assert "Another random log" in lines[2]
    assert "Final random log" in lines[3]
    
    print("Test passed: Duplicate /api/nxm/handoffs log was suppressed.")

if __name__ == "__main__":
    test_endpoint_filter()
