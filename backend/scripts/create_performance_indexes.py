"""Create large-table indexes online; safe to run repeatedly during deployment."""
from app.metrics_history import ensure_performance_indexes


def main() -> None:
    ensure_performance_indexes()
    print("performance indexes ready")


if __name__ == "__main__":
    main()
