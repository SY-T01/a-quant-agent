"""Short-lived public market-data worker; no keys, model calls or emails."""
import sys

from .watch import AKDaily


def main():
    if len(sys.argv) != 5:
        return 2
    try:
        provider = AKDaily.__new__(AKDaily)
        frame = provider._baostock_history_direct(*sys.argv[1:])
        print(frame.to_json(orient="split", date_format="iso"))
        return 0
    except Exception:
        print('{"error":"BaoStock public history unavailable"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
