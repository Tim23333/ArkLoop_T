import argparse
import logging

from src.logger import logger
from src.excel import Excel, StatusColor
from src.axis.json_loader import load_axis_from_json
from src.axis.axis_runner import AxisRunner
from src.logic.game_time import GameTime


def _run_json(axis_file: str, debug: bool, autoenter: bool):
    """Run the new JSON-driven path."""
    actions, settings = load_axis_from_json(axis_file)

    runner = AxisRunner(
        actions=actions,
        settings=settings,
        is_paused=lambda: False,
        autoenter=autoenter,
        show_error=lambda msg: logger.error(f"Axis error: {msg}"),
        set_result_color=lambda color: None,
        debug=debug,
    )
    runner.run()


def _run_excel(xlsm_file: str, debug: bool, autoenter: bool):
    """Run the legacy Excel-driven path."""
    from src.config import PerformActionConfig as actionconfig
    from src.logic.perform_action import perform_action, PerformLateError, UserPausedError
    from src.logic.calc_view import transform_map_to_view
    from src.logic.action import ActionType
    from src.logic.analyze_time import set_time_source
    from src.logic.ws_time_source import get_ws_time_source
    from src.cache import get_map_by_code, get_map_by_name
    from src.utils.error_to_log import ErrorToLog
    from src.logic.convert_pos import convert_position
    from src.logic.auto_enter import auto_enter

    try:
        logger.info(f"Excel file path: {xlsm_file}")
        excel = Excel(xlsm_file)
    except Exception as e:
        logger.error(f"Error occurred: {e}")
        logger.info("Press any key to exit.")
        input()
        raise

    def is_paused():
        return excel.is_paused()

    try:
        map_code = excel.get_setting('map_code')
        map_name = excel.get_setting('map_name')
        max_tick = excel.get_setting('max_tick')
        wait_time1 = excel.get_setting('wait_time1')
        wait_time2 = excel.get_setting('wait_time2')
        wait_time3 = excel.get_setting('wait_time3')
        bullet_threshold = excel.get_setting('bullet_threshold')
        frame_threshold = excel.get_setting('frame_threshold')

        # Game time now comes from the WS time source (external game-memory
        # reader). Start
        # the singleton and refuse to run when the feed is unavailable.
        ws = get_ws_time_source()
        ws.start()
        if not ws.wait_connected(timeout=5):
            raise ErrorToLog("时间源 WS 未连接，无法回放。请启动游戏时间服务。")
        set_time_source(ws)  # no-op compat; documents intent
        GameTime.set_tick_max(max_tick if max_tick is not None else 30)

        if wait_time1 is not None:
            actionconfig.MINIMUM_WAITTIME = wait_time1
            logger.debug(f"Set minimum wait time to {actionconfig.MINIMUM_WAITTIME}")
        if wait_time2 is not None:
            actionconfig.FRAME_WAITTIME = wait_time2
            logger.debug(f"Set frame wait time to {actionconfig.FRAME_WAITTIME}")
        if wait_time3 is not None:
            actionconfig.GENERAL_WAITTIME = wait_time3
            logger.debug(f"Set general wait time to {actionconfig.GENERAL_WAITTIME}")
        if bullet_threshold is not None:
            actionconfig.BULLET_THRESHOLD = bullet_threshold
            logger.debug(f"Set bullet threshold to {actionconfig.BULLET_THRESHOLD}")
        if frame_threshold is not None:
            actionconfig.FRAME_THRESHOLD = frame_threshold
            logger.debug(f"Set frame threshold to {actionconfig.FRAME_THRESHOLD}")

        if map_name is not None:
            map_data = get_map_by_name(map_name)
        elif map_code is not None:
            map_data = get_map_by_code(map_code)
        else:
            logger.error("No map specified.")
            raise ErrorToLog("未指定关卡。")

        view_data_front = transform_map_to_view(map_data, False)
        view_data_side = transform_map_to_view(map_data, True)
        map_height, map_width = map_data["height"], map_data["width"]

        operator_loc = {}
        operator_alias = {}

        if autoenter and not excel.is_paused():
            auto_enter()

        while not excel.is_paused():
            action = excel.get_current_action()

            if not action.is_valid():
                logger.warning(f"Invalid action: {action}")
                logger.info("Terminating the program")
                break

            convert_position(action, map_height, map_width)

            if action.action_type == ActionType.DEPLOY:
                operator_loc[action.oper] = action.tile_pos
                if action.alias is not None:
                    operator_loc[action.alias] = action.tile_pos
                logger.info(f"Memorized {action.oper} location at {operator_loc[action.oper]}")
            else:
                if action.tile_pos is None:
                    action.tile_pos = operator_loc.get(action.oper)
                    if action.tile_pos is not None:
                        logger.info(f"Auto set {action.oper} location to {action.tile_pos}")

            if action.alias is not None:
                operator_alias[action.alias] = action.oper
                logger.info(f"Memorized {action.alias} as an alias of {action.oper}")

            if action.oper in operator_alias:
                logger.info(f"Detected alias, replace {action.oper} with {operator_alias[action.oper]}")
                action.oper = operator_alias[action.oper]

            action.view_pos_front = view_data_front[action.tile_pos[1]][action.tile_pos[0]]
            action.view_pos_side = view_data_side[action.tile_pos[1]][action.tile_pos[0]]

            try:
                perform_action(action, is_paused)
                excel.set_result(StatusColor.SUCCESS)
            except PerformLateError as e:
                excel.set_result(StatusColor.WARNING)
                if e.actual_time > e.scheduled_time + GameTime(1, 0):
                    raise ErrorToLog("当前操作晚了超过一周期。疑似发生错误。请求人工接管。")
            except UserPausedError as e:
                raise ErrorToLog("用户停止。", False)
            except Exception as e:
                excel.set_result(StatusColor.FAILURE)
                raise

            excel.next_action()
    except ErrorToLog as e:
        logger.error(f"Error occurred: {e}")
        excel.show_error(f"{e}")
    except Exception as e:
        logger.error(f"Error occurred: {e}")
        excel.show_error(f"未定义错误：{e}")
    finally:
        set_time_source(None)
        excel.set_paused()
        if debug:
            logger.info("Press any key to exit.")
            input()


def main(axis_file, xlsm_file, debug, autoenter):
    if debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.WARNING)

    if axis_file:
        _run_json(axis_file, debug, autoenter)
    elif xlsm_file:
        _run_excel(xlsm_file, debug, autoenter)
    else:
        raise ValueError("Must provide either --axis or --xlsm")


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
