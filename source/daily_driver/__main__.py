def main() -> None:
    import sys

    from daily_driver.cli.cli import app

    sys.exit(app(sys.argv[1:]))


if __name__ == "__main__":
    main()
