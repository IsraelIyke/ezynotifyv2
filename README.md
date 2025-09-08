<p align="center">
  <b>Automated website monitoring bot with keyword detection, Supabase logging, and GitHub Actions scheduling ⚡</b>
</p>

---

## ✨ Features
- 🔎 Monitor websites for **updates or keyword matches**
- 📡 Uses **Selenium** (BeautifulSoup initially) for scraping
- ☁️ Stores results in **Supabase**
- 🔔 Sends instant notifications Telegram [Find telegram bot repo here](https://github.com/IsraelIyke/ezynotify-bot)
- 🕒 Automated with **GitHub Actions** (runs every 2 minutes)
- 🔒 Configurable via `.env` for security
- [Check Live Project here](https://t.me/ezynotify_bot)

---

## ⚙️ Installation

```bash
# Clone the repo. Refer to https://github.com/IsraelIyke/ezynotify-bot for the 2nd part of the bot. This part only hosts the github action yml
git clone https://github.com/IsraelIyke/ezynotifyv2.git
cd ezynotifyv2

# Install dependencies
pip install -r requirements.txt
```

---

## 🧩 Tech Stack
- Python
- Selenium (scraping)
- Supabase (storage)
- GitHub Actions (automation)
