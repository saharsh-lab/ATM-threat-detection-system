# AI-Based ATM Threat Detection System

[![Python](https://img.shields.io/badge/Python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey.svg)](https://palletsprojects.com/p/flask/)
[![YOLOv8](https://img.shields.io/badge/YOLO-v8n-orange.svg)](https://github.com/ultralytics/ultralytics)
[![OpenCV](https://img.shields.io/badge/OpenCV-Computer%20Vision-green.svg)](https://opencv.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An intelligent real-time surveillance and anomaly detection system for ATM vestibules using computer vision, YOLOv8 object detection, multi-person tracking, and a rule-based behavioral threat scoring engine with live web monitoring.

---

## 📌 Overview

ATM kiosks are vulnerable to various security threats including shoulder surfing, physical vandalism, robbery, and loitering. This system monitors real-time CCTV or video feeds, continuously analyzes human behaviors and spatial interactions, and classifies threat levels on a 5-tier risk scale (NORMAL, LOW, MEDIUM, HIGH, CRITICAL).

### Key Detection Capabilities

- 👥 **Multiple Persons in ATM Area**: Detects when two or more people are inside the restricted ATM perimeter (potential robbery, shoulder surfing, or coercion).
- ⏱️ **Loitering Detection**: Tracks person dwell time inside the vestibule and flags individuals lingering beyond safety thresholds.
- 🔨 **Physical Tampering & Vandalism**: Identifies abnormal postures, camera obstructions, and aggressive movement near the ATM terminal.
- 🔪 **Weapon & Dangerous Object Detection**: Alerts when bladed weapons, knives, or suspicious bottles/containers are brought into the ATM perimeter.
- 🎒 **Unattended Bags / Abandoned Luggage**: Identifies backpacks, suitcases, and handbags left unattended without an owner nearby.
- 🚨 **Automated Alerting**: Integrates real-time visual alerts and automated notification dispatch to the nearest police station or security monitoring hub.

---

## 🏗️ System Architecture

```text
       ┌────────────────────────┐
       │   CCTV / Video Feed    │
       └───────────┬────────────┘
                   │
                   ▼
       ┌────────────────────────┐
       │   Frame Preprocessing  │
       └───────────┬────────────┘
                   │
                   ▼
       ┌────────────────────────┐
       │  YOLOv8 Detection     │
       │  (Person, Bag, Weapon) │
       └───────────┬────────────┘
                   │
                   ▼
       ┌────────────────────────┐
       │ Multi-Object Tracking  │
       │ (Centroid & IoU Assoc) │
       └───────────┬────────────┘
                   │
                   ▼
       ┌────────────────────────┐
       │ Behavioral Engine      │
       │ (Dwell, Zone, Posture) │
       └───────────┬────────────┘
                   │
                   ▼
       ┌────────────────────────┐
       │ Threat Scoring (0-100) │
       │ (Dynamic Decay Filter) │
       └───────────┬────────────┘
                   │
       ┌───────────┴────────────┐
       ▼                        ▼
┌──────────────┐        ┌──────────────┐
│ Police Alert │        │ Web Monitor  │
│ Dispatch     │        │ Dashboard UI │
└──────────────┘        └──────────────┘
```

---

## 📊 Threat Scoring Engine

The threat evaluation pipeline combines several weighted heuristic checks to compute a dynamic score between `0` and `100`:

| Threat Trigger | Score Weight | Description |
| :--- | :---: | :--- |
| **Multiple Persons** | `+20` | More than 1 person present in the ATM restricted zone |
| **Loitering** | `+25` | Person stationary inside vestibule > 20 seconds |
| **Tampering / Vandalism** | `+30` | Abnormal aspect ratio & prolonged machine proximity |
| **Unattended Bag** | `+15` | Luggage left without an adjacent owner |
| **Restricted Zone Intrusion**| `+15` | Entry into defined high-security zone |
| **Weapon Object** | `+40` | Knife, scissors, or dangerous sharp object detected |
| **Threat Object** | `+25` | Suspicious bottle / combustible container |

### Risk Level Classification

- **CRITICAL** (`90 – 100`): Immediate danger; triggers automatic police notification banner.
- **HIGH** (`75 – 89`): High risk security violation; red visual alert.
- **MEDIUM** (`50 – 74`): Multiple suspicious behaviors; orange status.
- **LOW** (`25 – 49`): Minor irregularity or single mild anomaly; yellow status.
- **NORMAL** (`0 – 24`): Safe operating conditions; green status.

---

## 🗂️ Project Directory Structure

```text
atm_threat_detection/
├── app.py                   # Main Flask application and video streaming server
├── config.py                # System configuration, thresholds, and weights
├── requirements.txt         # Python package dependencies
├── yolov8n.pt               # YOLOv8 nano model weights
├── core/
│   ├── detector.py          # YOLOv8 object detection wrapper
│   ├── tracker.py           # Multi-person tracker with spatial dwell timing
│   ├── threat_engine.py     # Stateful threat scoring and smoothing engine
│   └── event_logger.py      # Timestamped audit and security incident log
├── data/
│   ├── annotations.py       # Ground truth evaluation interface
│   ├── labels.py            # Benchmark anomaly timestamps and labels
│   └── videos/              # Video dataset directory (.mp4)
├── static/
│   ├── css/style.css        # Glassmorphic dark security dashboard styling
│   └── js/dashboard.js      # Real-time state polling and UI updates
├── templates/
│   └── dashboard.html       # Web monitoring interface template
└── utils/
    └── helpers.py           # Bounding box rendering, zone drawing & JPEG encoding
```

---

## 🚀 Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/saharsh-lab/ATM-threat-detection-system.git
cd ATM-threat-detection-system
```

### 2. Set Up Virtual Environment

```bash
# macOS / Linux
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the Application

```bash
python app.py
```

Open your browser and navigate to:
```
http://localhost:5001
```

---

## 🖥️ Web Dashboard Features

- **Live CCTV Video Stream**: Displays processed video with real-time bounding boxes, restricted zone perimeter, person tracking IDs, and behavior badges.
- **Dynamic Threat Score Ring**: Circular SVG indicator displaying the real-time smoothed risk score (`0-100`).
- **Real-Time Risk Badge**: Dynamic color-coded status (`NORMAL`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`).
- **Active Threat Breakdown**: Bulleted list of current active threats.
- **Live Statistics**: Monitored count of persons, bags, frame number, and ground-truth anomaly indicator.
- **Event Audit Log**: Historical event log recording security changes with timestamps.
- **Video Switcher**: Dropdown selector allowing operators to switch between test video feeds seamlessly without restarting the server.

---

## 🔌 REST API Endpoints

- `GET /`: Renders the main surveillance dashboard.
- `GET /video_feed`: MJPEG multipart stream of the processed video feed.
- `GET /threat_data`: Returns current frame risk evaluation JSON:
  ```json
  {
    "score": 75,
    "level": "HIGH",
    "reasons": ["Multiple persons near ATM", "Loitering detected"],
    "color": "#dc143c",
    "stats": { "persons": 2, "bags": 0, "frame": 120 },
    "ground_truth": true,
    "log": ["22:15:30 - Risk increased to HIGH"]
  }
  ```
- `POST /switch_video`: Dynamically changes active video feed:
  ```json
  { "filename": "85.mp4" }
  ```

---

## 📜 License

This project is licensed under the [MIT License](LICENSE).