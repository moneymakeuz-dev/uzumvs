import secrets
import socket
from pathlib import Path


def available_port(start: int) -> int:
    for port in range(start, start + 100):
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local port found")


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    destination = root / ".env"
    if destination.exists():
        print("Existing .env retained. No settings changed.")
        return
    password = secrets.token_urlsafe(32)
    db_port = available_port(55432)
    web_port = available_port(8000)
    url = f"postgresql+psycopg://karto:{password}@127.0.0.1:{db_port}"
    settings = {
        "APP_ENV": "development", "APP_BASE_URL": f"http://127.0.0.1:{web_port}",
        "POSTGRES_PASSWORD": password, "POSTGRES_PORT": str(db_port),
        "DATABASE_URL": f"{url}/karto", "TEST_DATABASE_URL": f"{url}/karto_test",
        "AI_PROVIDER": "disabled", "MAIL_BACKEND": "file",
    }
    with destination.open("x", encoding="utf-8") as output:
        output.write("\n".join(f"{key}={value}" for key, value in settings.items()) + "\n")
    print(f"Local configuration created. Database port: {db_port}. App port: {web_port}.")
    print("AI is disabled until configured. Secrets were not printed.")


if __name__ == "__main__":
    main()