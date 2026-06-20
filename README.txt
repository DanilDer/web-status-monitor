Web Status Monitor
==================

A command-line web status monitor built with raw TCP sockets in Python.
Implements HTTP/1.0 and HTTPS (via TLS) from scratch — no HTTP client libraries used.

Features
--------
- Parses and fetches HTTP and HTTPS URLs
- Reports 2XX, 3XX, 4XX status codes
- Follows 301/302 redirects automatically
- Fetches and reports referenced <img> objects from HTML responses
- Handles network errors gracefully (dead servers, timeouts, connection failures)
- HTTPS support via Python built-in ssl library

Usage
-----
  python monitor.py urls-file

where urls-file is a plain text file with one URL per line.

Example urls-file:
  http://google.com/
  http://google.com/404
  https://www.fiu.edu

Example output:
  URL: http://google.com/
  Status: 301 Moved Permanently
  Redirected URL: http://www.google.com/
  Status: 200 OK

  URL: http://google.com/404
  Status: 404 Not Found

Language
--------
Python 3

SSL Library
-----------
Python built-in ssl module (standard library, no install needed)

Requirements
------------
Python 3.x — no third-party packages required
