import sys
import os
import time
import json
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# Paths
AIP_DIR = os.path.abspath("dexia/aip")
PENDING_PATH = os.path.join(AIP_DIR, "pending_proposals.json")
RECIPES_PATH = os.path.join(AIP_DIR, "recipes.json")
# Where headless screenshots land. Defaults to a repo-local ./artifacts dir so
# the script is portable; override with DEXIA_ARTIFACTS_DIR for a custom path.
ARTIFACTS_DIR = os.environ.get("DEXIA_ARTIFACTS_DIR", os.path.abspath("artifacts"))

# Create mock proposal
MOCK_PROPOSAL = [
  {
    "id": "prop_test_123",
    "status": "PENDING",
    "scenario": "SEAD Swarm Mission",
    "from_version": 1.1,
    "to_version": 1.2,
    "trigger_event": "Comms outage risk detected near waypoint Bravo",
    "root_cause": "High terrain obstruction blocking direct line-of-sight back to the base station.",
    "chain_of_thought": [
      "Analyze current altitude: 15.0m is below the line-of-sight threshold.",
      "Calculate minimum altitude for clear signal: 25.0m.",
      "Check max altitude limit in current rules: 50.0m (sufficient).",
      "Formulate recommendation: adjust target priority to prioritising radar suppression."
    ],
    "changes": [
      {
        "rule": "min_scatter_distance_m",
        "from": 50,
        "to": 60
      },
      {
        "rule": "max_altitude_m",
        "from": 50,
        "to": 65
      }
    ],
    "proposed_update": {
      "min_scatter_distance_m": 60,
      "max_altitude_m": 65
    },
    "rationale": "Increasing the minimum scatter distance prevents drone collisions while ascending to obtain line-of-sight communication."
  }
]

def main():
    print("=" * 80)
    print("DEXIA GCS HUD - Headless UI Interaction Test (Host-Docker Shared)")
    print("=" * 80)

    # 1) Backup original recipes and pending proposals
    print("[1] Backing up existing files on host...")
    recipes_backup = read_json(RECIPES_PATH)
    pending_backup = read_json(PENDING_PATH)
    
    # Reset recipes.json to version 1.1 for deterministic test
    test_recipes = {
      "scenario": "SEAD",
      "version": 1.1,
      "rules": {
        "min_scatter_distance_m": 50,
        "max_altitude_m": 50,
        "target_priority": "AA_RADAR"
      }
    }
    write_json(RECIPES_PATH, test_recipes)
    write_json(PENDING_PATH, MOCK_PROPOSAL)
    print("    Mock proposal written on host. Initial recipe version reset to 1.1.")
    
    # Reset/clear command queue to disarm simulation and recall drones
    write_json("commands.json", [{"id": "init_clear", "ts": "2026-06-04T00:00:00.000Z", "action": "clear"}])
    print("    Cleared scenario and disarmed simulation to start fresh.")
    time.sleep(2)

    print("\n[2] Initializing Chrome Webdriver in headless mode...")
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    try:
        url = "http://localhost:3000"
        print(f"    Navigating to {url}...")
        driver.get(url)
        time.sleep(3) # Wait for page load and proposals poll
        
        # --- Screen 1: Verify HITL Approval Modal is displayed ---
        print("\n[3] Checking for HITL Commander Approval Modal...")
        modal_header_xpath = "//*[contains(text(), 'AI COMMAND STAFF PROPOSAL')]"
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.XPATH, modal_header_xpath))
        )
        print("    Success: HITL Proposal Modal is visible on screen!")
        
        # Save first screenshot
        scr1 = os.path.join(ARTIFACTS_DIR, "gcs_screen_1_proposal.png")
        driver.save_screenshot(scr1)
        print(f"    Saved: {scr1}")
        
        # --- Click Approve ---
        print("\n[4] Clicking APPROVE (승인) button...")
        approve_btn_xpath = "//button[contains(text(), 'APPROVE')]"
        approve_btn = driver.find_element(By.XPATH, approve_btn_xpath)
        approve_btn.click()
        
        # Wait for toast and modal removal
        time.sleep(2)
        
        # Save second screenshot (toast message)
        scr2 = os.path.join(ARTIFACTS_DIR, "gcs_screen_2_approved.png")
        driver.save_screenshot(scr2)
        print(f"    Saved: {scr2}")
        
        # Verify recipes.json update directly on the host (due to docker shared volume)
        updated_recipes = read_json(RECIPES_PATH)
        print(f"    Updated recipes.json Version: {updated_recipes.get('version')}")
        print(f"    Rules: {updated_recipes.get('rules')}")
        assert updated_recipes.get('version') == 1.2, "Recipe version did not update!"
        print("    Success: recipes.json correctly updated to v1.2!")
        
        # --- Screen 3: Open Scenario Builder Bar ---
        print("\n[5] Opening Scenario Builder Bar (편성)...")
        builder_btn_xpath = "//button[contains(text(), '편성')]"
        builder_btn = driver.find_element(By.XPATH, builder_btn_xpath)
        builder_btn.click()
        time.sleep(1)
        
        # Click "드론 배치" tool
        drone_tool_xpath = "//button[contains(text(), '드론 배치')]"
        drone_tool_btn = driver.find_element(By.XPATH, drone_tool_xpath)
        drone_tool_btn.click()
        time.sleep(0.5)
        
        # Select "Recon Hexa" profile from the dropdown
        print("    Selecting Recon Hexa profile from the builder dropdown...")
        try:
            from selenium.webdriver.support.ui import Select
            select_element = driver.find_element(By.XPATH, "//select")
            select = Select(select_element)
            select.select_by_value("prof_recon_hexa")
            print("    Selected prof_recon_hexa successfully.")
            time.sleep(0.5)
        except Exception as select_err:
            print(f"    Failed to select Recon Hexa: {select_err}")
        
        # Click on the map canvas to place a drone
        print("    Clicking on tactical map to deploy a drone...")
        try:
            map_canvas = driver.find_element(By.CLASS_NAME, "maplibregl-canvas")
            from selenium.webdriver.common.action_chains import ActionChains
            actions = ActionChains(driver)
            # Click near the center-left of the map to place friendly drone
            actions.move_to_element_with_offset(map_canvas, -50, -50).click().perform()
            print("    Successfully clicked map canvas.")
            time.sleep(2)
        except Exception as map_err:
            print(f"    Map click failed: {map_err}")

        # Save third screenshot (builder open & drone placed)
        scr3 = os.path.join(ARTIFACTS_DIR, "gcs_screen_3_builder_open.png")
        driver.save_screenshot(scr3)
        print(f"    Saved: {scr3}")
        
        # --- Screen 4: Click ACTIVATE to Arm simulation ---
        print("\n[6] Activating simulation (이륙)...")
        # Wait up to 5 seconds for ACTIVATE button to be present and clickable
        activate_btn_xpath = "//button[contains(text(), 'ACTIVATE')]"
        activate_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, activate_btn_xpath))
        )
        activate_btn.click()
        
        # Verify armed state on front-end (wait up to 15 seconds)
        print("    Waiting for GCS state to change to ARMED...")
        status_tag_xpath = "//*[contains(text(), 'ARMED')]"
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.XPATH, status_tag_xpath))
            )
            print("    Success: Drone GCS armed successfully!")
        except Exception as wait_exc:
            print("\n    Timeout waiting for ARMED. Fetching page status...")
            try:
                # Find any text resembling standby/armed
                body_text = driver.find_element(By.TAG_NAME, "body").text
                print("--- Current Body Text (Excerpt) ---")
                safe_text = body_text[:1500].encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8', errors='replace')
                print(safe_text) # First 1500 chars
            except Exception as e2:
                print(f"Could not read body text: {e2}")
            raise wait_exc
        
        # Save fourth screenshot (armed state)
        scr4 = os.path.join(ARTIFACTS_DIR, "gcs_screen_4_armed.png")
        driver.save_screenshot(scr4)
        print(f"    Saved: {scr4}")

    except Exception as e:
        print(f"\nError during UI simulation: {e}")
        try:
            print("\n--- Chrome Console Logs ---")
            for entry in driver.get_log('browser'):
                print(entry)
        except Exception as e_log:
            print(f"Could not fetch console logs: {e_log}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n[7] Cleaning up and restoring backups on host...")
        driver.quit()
        # Restore backups
        write_json(RECIPES_PATH, recipes_backup)
        write_json(PENDING_PATH, pending_backup)
        print("    Restore complete.")
        print("=" * 80)

def read_json(p):
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def write_json(p, obj):
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2)

if __name__ == "__main__":
    main()
