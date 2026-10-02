from src.analyzer import find_incident_timeline
from src.agent import generate_rca_summary

def main():
    incident_data = find_incident_timeline()
    report_text = generate_rca_summary(incident_data)
    print(report_text)

if __name__ == "__main__":
    main()