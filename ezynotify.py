"""Run one bounded EzyNotify worker pass. Configuration comes from secrets/.env."""
from dotenv import load_dotenv

if __name__ == '__main__':
    load_dotenv()
    from monitor.worker import main
    raise SystemExit(main())
