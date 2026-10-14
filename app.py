# app.py
# SentinelX entry point (local / on-prem). Starts the camera workers, the
# REST API and the dashboard.
#
#   python manage.py init      # first run: database + admin account
#   python app.py              # http://127.0.0.1:5001
#
# The original single-file dashboard API (/video_feed, /threat_data,
# /switch_video) is still served for backward compatibility (web/pages.py).

import config
from web import create_app

app = create_app()

if __name__ == "__main__":
    # threaded=True: MJPEG streams are long-lived requests
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=False, threaded=True)
