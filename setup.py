import json
import os

def setup_contest():
    print("Welcome to Contest Setup!")
    print("------------------------")
    
    # Get contest name
    contest_name = input("Enter the name of your contest: ").strip()
    
    print("------------------------ QUESTION 2 ------------------------")
    time_zone = input("Enter the time zone for the contest (e.g., 'UTC', 'America/New_York'): ").strip()
    
    # Create config directory if it doesn't exist
    if not os.path.exists('config'):
        os.makedirs('config')
    
    # Save configuration
    config = {
        'contest_name': contest_name,
        'time_zone': time_zone    
    }
    
    with open('config/contest_config.json', 'w') as f:
        json.dump(config, f, indent=4)
    
    print(f"\nContest name set to: {contest_name}")
    print(f"\nTime Zone set to: {time_zone}")
    print("Configuration saved successfully!")

if __name__ == "__main__":
    setup_contest() 