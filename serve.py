import http.server
import socketserver
import os
import json
import time

PORT = 8085
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
TELEMETRY_FILE = os.path.join(DIRECTORY, "live_flight_telemetry.json")
AI_DIAGNOSTICS_FILE = os.path.join(DIRECTORY, "live_ai_diagnostics.json")

class TelemetryHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.path == '/api/telemetry':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                data['server_timestamp'] = time.time()
                data['server_time_str'] = time.strftime('%Y-%m-%d %H:%M:%S')

                # Write live telemetry
                with open(TELEMETRY_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                # If AI diagnostic payload exists, separate for fast audit
                if 'neural_diagnostics' in data:
                    with open(AI_DIAGNOSTICS_FILE, 'w', encoding='utf-8') as f:
                        json.dump(data['neural_diagnostics'], f, ensure_ascii=False, indent=2)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status":"ok","received":true}')
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

socketserver.TCPServer.allow_reuse_address = True

if __name__ == '__main__':
    try:
        with socketserver.TCPServer(("", PORT), TelemetryHandler) as httpd:
            print(f"Serving HTTP on port {PORT} with Live Telemetry Pipeline (http://127.0.0.1:{PORT}/flight_simulator_3d.html)...", flush=True)
            httpd.serve_forever()
    except Exception as e:
        print(f"Server error: {e}", flush=True)
