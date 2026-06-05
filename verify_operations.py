import sys
import os
import time
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

def operate_system():
    print("--- 1. Testing API Endpoints ---")
    base_url = "http://localhost:8000"
    headers = {"X-Dexia-Key": "dexia-commander"}  # Correct key from auth.py
    
    # Check health
    try:
        res = requests.get(f"{base_url}/api/health")
        print(f"Health: {res.status_code} - {res.json()}")
    except Exception as e:
        print(f"API Health check failed: {e}")

    # Set enemy
    try:
        res = requests.post(f"{base_url}/api/sim/enemy", json={"x": 5000, "y": 1500}, headers=headers)
        print(f"Set Enemy: {res.status_code}")
    except Exception as e:
        print(f"Set Enemy failed: {e}")

    # Set friendly
    try:
        res = requests.post(f"{base_url}/api/sim/friendly", json={"x": -1000, "y": 0}, headers=headers)
        print(f"Set Friendly: {res.status_code}")
    except Exception as e:
        print(f"Set Friendly failed: {e}")

    # Deploy drones
    try:
        res = requests.post(f"{base_url}/api/sim/deploy", json={"x": 0, "y": 0, "z": 0.3, "role": "recon"}, headers=headers)
        print(f"Deploy Recon: {res.status_code}")
        res = requests.post(f"{base_url}/api/sim/deploy", json={"x": 10, "y": 0, "z": 0.3, "role": "kami"}, headers=headers)
        print(f"Deploy Kami: {res.status_code}")
    except Exception as e:
        print(f"Deploy failed: {e}")

    # Activate scenario
    try:
        res = requests.post(f"{base_url}/api/sim/activate", headers=headers)
        print(f"Activate: {res.status_code}")
    except Exception as e:
        print(f"Activate failed: {e}")
        
def capture_hud():
    print("--- 2. Capturing HUD Screenshots ---")
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    try:
        # Check Dashboard (index.js)
        driver.get("http://localhost:3000")
        time.sleep(5)  # Wait for telemetry to load and sim to update
        driver.save_screenshot(os.path.abspath("dashboard_active.png"))
        print("Dashboard screenshot saved: dashboard_active.png")
        
        # Check Operations Center
        driver.get("http://localhost:3000/operations")
        time.sleep(5)
        driver.save_screenshot(os.path.abspath("operations_active.png"))
        print("Operations Center screenshot saved: operations_active.png")
        
    except Exception as e:
        print(f"Selenium error: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    operate_system()
    time.sleep(2)
    capture_hud()
