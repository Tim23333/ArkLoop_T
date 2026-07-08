from src.main import main
import argparse

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='PRTS+')
    parser.add_argument('--axis', type=str, help='The path to the JSON axis file.')
    parser.add_argument('--xlsm', type=str, help='The path to the Excel file.')
    parser.add_argument('--debug', action='store_true', help='Run in debug mode.')
    parser.add_argument('--autoenter', action='store_true', help='Run in auto enter mode.')
    args = parser.parse_args()

    if not args.axis and not args.xlsm:
        parser.error("Either --axis or --xlsm must be provided.")

    main(args.axis, args.xlsm, args.debug, args.autoenter)
