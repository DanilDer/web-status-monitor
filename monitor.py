import socket
import ssl
import sys
import re

# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def parse_url(url):
    """
    Parse a URL into (scheme, host, port, path).
    Returns None if the URL is malformed.
    """
    url = url.strip()

    if url.startswith("https://"):
        scheme = "https"
        rest = url[len("https://"):]
        default_port = 443
    elif url.startswith("http://"):
        scheme = "http"
        rest = url[len("http://"):]
        default_port = 80
    else:
        return None

    # Split host (+ optional port) from path
    slash_idx = rest.find("/")
    if slash_idx == -1:
        host_part = rest
        path = "/"
    else:
        host_part = rest[:slash_idx]
        path = rest[slash_idx:]

    # Separate port from host if present
    if ":" in host_part:
        host, port_str = host_part.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            return None
    else:
        host = host_part
        port = default_port

    if not host:
        return None

    return scheme, host, port, path


# ---------------------------------------------------------------------------
# Raw HTTP over TCP (and optionally TLS)
# ---------------------------------------------------------------------------

TIMEOUT = 10          # seconds
MAX_REDIRECTS = 10    # safety limit


def make_socket(scheme, host, port):
    """
    Open a TCP socket (with TLS wrap for HTTPS).
    Returns the socket, or raises an OSError on failure.
    """
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.settimeout(TIMEOUT)
    raw.connect((host, port))

    if scheme == "https":
        ctx = ssl.create_default_context()
        return ctx.wrap_socket(raw, server_hostname=host)

    return raw


def send_request(sock, host, path):
    """
    Send a minimal HTTP/1.0 GET request and return the raw response bytes.
    Using HTTP/1.0 avoids chunked-encoding and keep-alive complications.
    """
    request = (
        f"GET {path} HTTP/1.0\r\n"
        f"Host: {host}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )
    sock.sendall(request.encode())

    # Read until the server closes the connection
    chunks = []
    while True:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        chunks.append(chunk)

    return b"".join(chunks)


def fetch(scheme, host, port, path):
    """
    Open a connection, send a GET, and return (status_line, headers_dict, body_str).
    Raises OSError / socket.* exceptions on network problems.
    """
    sock = make_socket(scheme, host, port)
    try:
        raw = send_request(sock, host, path)
    finally:
        try:
            sock.close()
        except Exception:
            pass

    if not raw:
        raise OSError("Empty response from server")

    # Split head from body on the first blank line
    if b"\r\n\r\n" in raw:
        head, body_bytes = raw.split(b"\r\n\r\n", 1)
    elif b"\n\n" in raw:
        head, body_bytes = raw.split(b"\n\n", 1)
    else:
        head = raw
        body_bytes = b""

    head_text = head.decode("utf-8", errors="replace")
    body_text = body_bytes.decode("utf-8", errors="replace")

    lines = head_text.split("\r\n") if "\r\n" in head_text else head_text.split("\n")
    status_line = lines[0].strip()

    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, _, val = line.partition(":")
            headers[key.strip().lower()] = val.strip()

    return status_line, headers, body_text


# ---------------------------------------------------------------------------
# Status code extraction
# ---------------------------------------------------------------------------

def status_from_line(status_line):
    """Return the numeric status code (int) from an HTTP status line, or None."""
    parts = status_line.split()
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return None


def status_text_from_line(status_line):
    """Return everything after 'HTTP/x.x NNN', i.e. the reason phrase."""
    parts = status_line.split(None, 2)
    if len(parts) == 3:
        return f"{parts[1]} {parts[2]}"
    elif len(parts) == 2:
        return parts[1]
    return status_line


# ---------------------------------------------------------------------------
# Image (<img>) extraction from HTML
# ---------------------------------------------------------------------------

IMG_PATTERN = re.compile(r"<img\b[^>]*\bsrc\s*=\s*['\"]?([^'\">\s]+)", re.IGNORECASE)


def extract_img_urls(html, base_scheme, base_host, base_port, base_path):
    """
    Find all <img src=...> references in html and return absolute URLs.
    Relative paths are resolved against the base URL.
    """
    seen = set()
    urls = []

    for m in IMG_PATTERN.finditer(html):
        src = m.group(1).strip()

        # Skip inline base64 data URIs — they are not fetchable URLs
        if src.startswith("data:"):
            continue

        if src.startswith("http://") or src.startswith("https://"):
            absolute = src
        elif src.startswith("//"):
            absolute = base_scheme + ":" + src
        elif src.startswith("/"):
            # Absolute path on the same server
            if base_port in (80, 443):
                absolute = f"{base_scheme}://{base_host}{src}"
            else:
                absolute = f"{base_scheme}://{base_host}:{base_port}{src}"
        else:
            # Relative to the current directory
            base_dir = base_path.rsplit("/", 1)[0] + "/"
            if base_port in (80, 443):
                absolute = f"{base_scheme}://{base_host}{base_dir}{src}"
            else:
                absolute = f"{base_scheme}://{base_host}:{base_port}{base_dir}{src}"

        # Deduplicate — only report each unique image URL once
        if absolute not in seen:
            seen.add(absolute)
            urls.append(absolute)

    return urls


# ---------------------------------------------------------------------------
# Core monitor logic for a single URL
# ---------------------------------------------------------------------------

def monitor_url(url):
    """
    Fetch url, handle redirects, fetch referenced images, and print results.
    """
    print(f"URL: {url}")

    parsed = parse_url(url)
    if parsed is None:
        print("Status: Network Error")
        print()
        return

    scheme, host, port, path = parsed

    # ---- Fetch the original URL ----------------------------------------
    try:
        status_line, headers, body = fetch(scheme, host, port, path)
    except Exception:
        print("Status: Network Error")
        print()
        return

    code = status_from_line(status_line)
    if code is None:
        print("Status: Network Error")
        print()
        return

    status_display = status_text_from_line(status_line)
    print(f"Status: {status_display}")

    # ---- 3XX redirect handling -----------------------------------------
    redirects = 0
    final_scheme, final_host, final_port, final_path = scheme, host, port, path
    final_body = body

    while code is not None and 300 <= code <= 399 and redirects < MAX_REDIRECTS:
        location = headers.get("location", "")
        if not location:
            break

        print(f"Redirected URL: {location}")

        redir_parsed = parse_url(location)
        if redir_parsed is None:
            print("Status: Network Error")
            print()
            return

        r_scheme, r_host, r_port, r_path = redir_parsed

        try:
            status_line, headers, body = fetch(r_scheme, r_host, r_port, r_path)
        except Exception:
            print("Status: Network Error")
            print()
            return

        code = status_from_line(status_line)
        if code is None:
            print("Status: Network Error")
            print()
            return

        status_display = status_text_from_line(status_line)
        print(f"Status: {status_display}")

        final_scheme, final_host, final_port, final_path = r_scheme, r_host, r_port, r_path
        final_body = body
        redirects += 1

    # ---- Referenced image handling (only on 2XX responses) -------------
    if code is not None and 200 <= code <= 299:
        img_urls = extract_img_urls(
            final_body, final_scheme, final_host, final_port, final_path
        )
        for img_url in img_urls:
            print(f"Referenced URL: {img_url}")
            img_parsed = parse_url(img_url)
            if img_parsed is None:
                print("Status: Network Error")
                continue
            i_scheme, i_host, i_port, i_path = img_parsed
            try:
                i_status_line, _, _ = fetch(i_scheme, i_host, i_port, i_path)
                i_code = status_from_line(i_status_line)
                if i_code is None:
                    print("Status: Network Error")
                else:
                    print(f"Status: {status_text_from_line(i_status_line)}")
            except Exception:
                print("Status: Network Error")

    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} urls-file")
        sys.exit(1)

    urls_file = sys.argv[1]

    try:
        with open(urls_file, "r") as f:
            urls = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Error: file '{urls_file}' not found.")
        sys.exit(1)

    for url in urls:
        monitor_url(url)


if __name__ == "__main__":
    main()
