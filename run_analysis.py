import asyncio
from src.analyzer import find_incident_timeline
from src.agent import generate_rca_summary

async def main():
    incident_data = find_incident_timeline()
    report_text = await generate_rca_summary(incident_data)
    print(report_text)

if __name__ == "__main__":
    asyncio.run(main())
