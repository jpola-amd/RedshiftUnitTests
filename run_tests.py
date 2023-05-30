import platform as platform
from typing import Tuple, List
import re as re
import colorama as color_terminal
from colorama import Fore, Style
import argparse
import pprint as pprint
from pprint import PrettyPrinter
from pathlib import Path
import os as os
from datetime import datetime
from subprocess import Popen, PIPE
import shutil as shutil
import json as json
from dataclasses import dataclass
from abc import ABC, abstractmethod

import cv2 as cv
import matplotlib.pyplot as plt
import matplotlib
from skimage.metrics import structural_similarity as ssim
from skimage.metrics import mean_squared_error

USE_MULTIPROCESSING = True
try:
    from multiprocessing import Pool, Process
except ImportError:
    print('multiprocessing module was not found. Will use single threaded version')
    USE_MULTIPROCESSING = False


matplotlib.use('Agg')

EXIT_SUCCESS = 0
EXIT_FAILURE = -1


def print_error(msg: str):
    print(f'{Fore.RED}ERROR: {msg}{Style.RESET_ALL}')


def print_msg(msg: str, color=None):
    if color:
        print(f"{color}{msg}{Style.RESET_ALL}")
    else:
        print(f"{msg}")


def get_os_tag() -> str:
    if platform.system() == "Linux":
        return "linux"
    elif platform.system() == "Windows":
        return "win"
    else:
        raise RuntimeError("{} is not supported".format(platform.system()))


def process_test_files(test_json, tests_list) -> None:
    try:
        with open(test_json, 'r') as test_file:
            conf = json.load(test_file)
        tests = conf['tests']
        for t in tests:
            if 'include' in t:
                process_test_files(Path(test_json).parent /
                                   t['include'], tests_list)
            else:
                tests_list.append(t)
    except IOError as e:
        raise e


def load_test_files(tests: list) -> list:
    print(f'{Fore.GREEN}Processing files from{Style.RESET_ALL}: {tests}:', end= ' ')
    scene_files = []
    try:
        for json_test in tests:
            process_test_files(json_test, scene_files)
    except IOError as io_err:
        print_error(repr(io_err))
        exit(EXIT_FAILURE)
    print(f'{Fore.BLUE}{len(scene_files)}{Style.RESET_ALL} tests were found')
    return scene_files


def execute_process(params: list, user_env=None) -> int:
    params_str = [str(p) for p in params]
    process = Popen(params_str, env=user_env,
                    stdout=PIPE, stderr=PIPE, shell=False)
    stdout, stderr = process.communicate()
    return process.returncode


def get_latest_log_path() -> Path:
    if get_os_tag() == "win":
        return Path("C:\\ProgramData\\Redshift\\Log\\Log.Latest.0")
    else:
        return Path.home() / 'redshift/log/log.latest.0'


def analyze_latest_log(log_file: Path) -> Tuple[bool, str]:
    error_message = 'Redshift encountered an unrecoverable error during rendering and has been disabled.'
    try:
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as file:
            log = file.read()
            if log.find(error_message) != -1:
                return False, error_message
            regex = re.search('=\n(ASSERT FAILED.*)\n=', log, re.DOTALL)
            if regex:
                msg = regex.group(1)
                return False, msg
        return True, "Success"
    except IOError as err:
        return False, "Log not found"


@dataclass(init=False, eq=False, order=False, repr=True)
class ExecutionParameters:
    def __init__(self, args):
        self.reference = args.reference
        self.tests = args.test
        self.no_delete = args.no_delete
        self.treshold = args.treshold
        self.program = args.program
        self.gpu = args.gpu
        self.root_path = Path(__file__).resolve().parent

        try:
            with open(args.config, 'r') as cfg:
                self.config = json.load(cfg)
            with open(args.user_config, 'r') as cfg:
                self.user_config = json.load(cfg)
        except IOError as err:
            raise err
        except json.decoder.JSONDecodeError as jerr:
            raise jerr

        is_valid, msg = self.validate()
        if not is_valid:
            raise ValueError(msg)

    # def __repr__(self) -> str:
    #    return f"program: {self.program}, test: {self.tests}, gpu: {self.gpu}, reference: {self.reference} treshold: {self.treshold}, no_delete: {self.no_delete}"

    def print_config(self) -> None:
        pp = PrettyPrinter(indent=4)
        pp.pprint(self.config)

    def print_user_config(self) -> None:
        pp = PrettyPrinter(indent=4)
        pp.pprint(self.user_config)

    def validate(self) -> (bool, str):
        # config
        if not "required" in self.config:
            return False, "'required' section is missing in config"
        if not "required" in self.user_config:
            return False, "'required' section is missing in user config"
        if not "maya_project_root" in self.user_config['required']:
            return False, "'maya_project_root' is missing in user config"
        if not 'redshift_project_root' in self.user_config['required']:
            return False, "'redshift_project_root' is missing in user config"

        # paths
        p = Path(self.config['required']['redshiftCmdLine'])
        if not p.exists():
            return False, f"Config: Path to redshiftCmdLine does not exists {p}"
        p = Path(self.config['required']['redshiftBenchmark'])
        if not p.exists():
            return False, f"Config: Path to redshiftBenchmark does not exists {p}"
        p = Path(self.user_config['required']['redshift_project_root'])
        if not p.exists():
            return False, f"User config: Path to Redshift scenes [redshift_project_root] does not exists {p}"
        p = Path(self.user_config['required']['maya_project_root'])
        if not p.exists():
            return False, f"User config: Path to Maya scenes [maya_project_root] does not exists {p}"

        return True, None

    def get_executable(self) -> Path:
        kind = self.program
        if kind == 'redshiftBenchmark':
            return self.config['required']['redshiftBenchmark']
        elif kind == 'redshiftCmdLine':
            return self.config['required']['redshiftCmdLine']
        elif kind == 'maya':
            return self.config['required']['maya_batch']
        elif kind == 'xsi':
            return self.config['required']['xsi_batch']
        else:
            return Path()


def parse_command_line_args() -> ExecutionParameters:
    class ExtendAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            items = getattr(namespace, self.dest) or []
            items.extend(values)
            setattr(namespace, self.dest, items)

    DEFAULT_CONFIG_FILE = f'config/config.{get_os_tag()}.json'
    DEFAULT_USER_CONFIG_FILE = f'config/config.{get_os_tag()}.user.json'

    parser = argparse.ArgumentParser(description='Run Redshift unit tests.')
    parser.register('action', 'extend', ExtendAction)
    parser.add_argument('--reference', action='store_true', help='Use to generate reference images for given "program"')
    parser.add_argument('--config', default=DEFAULT_CONFIG_FILE, help='Main configuration file to use')
    parser.add_argument('--user-config', default=DEFAULT_USER_CONFIG_FILE, help='User config to use')
    parser.add_argument('--test', action='append', required=True, help='Test to execute')
    parser.add_argument('--no-delete', action='store_true', help='deprecated')
    parser.add_argument('--gpu', nargs='+', required=True, action='extend', help='GPU to execute the program. For multi-GPU separete with comma --gpu 1,2,3')
    parser.add_argument("--treshold", type=float, default=0.95, help="Mean Square Root [mse] value above which the image is considered incorrect")
    parser.add_argument("--program", choices=['redshiftCmdLine',
                        'redshiftBenchmark', 'maya'], required=True, help='Choose program to execute the tests')

    args = parser.parse_args()
    parameters = ExecutionParameters(args)
    return parameters


@dataclass(repr=True)
class Scene:
    def __init__(self, param: dict, root_path: Path):
        self.name = param["test_name"]
        self.path = root_path / 'scenes' / Path(param["path_to_scene"])
        self.type = self.path.suffix
        self.frames = ('1', '1') if not "frames" in param else param["frames"]
        self.skippostfx = "false" if not "skippostfx" in param else param["skippostfx"]


'''
Keeps the information about the scenes
that Succeeded, Failed or skipped
'''


class ExecutionResults:
    def __init__(self):
        self.info = {
            "summary": {},  # dict
            "success": [],
            "failed": [],
            "skipped": []
        }

    def add_result(self, type: str, scene: Scene, err_msg: str = "") -> None:
        self.info[type].append((scene.name, str(scene.path), err_msg))

    def _summary(self) -> None:
        summary = {key: len(value)
                   for key, value in self.info.items() if key != 'summary'}
        self.info["summary"] = summary

    def save(self, file: Path) -> None:
        self._summary()
        json_data = json.dumps(self.info, indent=2)
        try:
            with open(file, 'w') as json_file:
                json_file.write(json_data)
        except IOError as io_err:
            print_error(
                f"Could not save execution info to {file} [{repr(io_err)}]")


class Task(ABC):
    def __init__(self, params: ExecutionParameters):
        self.params = params
        self.scenes = load_test_files(params.tests)
        self.env = os.environ.copy()
        self.reference_path = params.root_path / 'references'
        self.results_path = Path()

    @abstractmethod
    def execute(self):
        pass

    @abstractmethod
    def handle_result(self):
        pass

    @abstractmethod
    def init_folders(self):
        pass


class RedshiftCmdLineTask(Task):
    def __init__(self, params: ExecutionParameters):
        super().__init__(params)
        self.init_folders()
        self.execution_results = ExecutionResults()
        self.env['REDSHIFT_PATHOVERRIDE_STRING'] = self.params.user_config['required']['redshift_project_root']
        msg = f'{Fore.MAGENTA}Executing test for'\
              f'{Fore.GREEN} {params.program}{Style.RESET_ALL}'
        print(msg)

    def execute(self):
        count = len(self.scenes)
        success = 0
        skipped = 0
        errors = 0
        index = 0
        for scene_params in self.scenes:
            index += 1
            scene = Scene(scene_params, self.params.root_path)
            if not scene.path.exists():
                err_msg = f"{scene.path} do not exists"
                print_error(err_msg)
                self.execution_results.add_result("failed", scene, err_msg)
                errors += 1
                continue
            if not scene.type == ".rs":
                warn_msg = f'{Fore.YELLOW}Warning: {Fore.GREEN}{scene.path}{Style.RESET_ALL} is not redshift scene'
                print(warn_msg)
                self.execution_results.add_result('skipped', scene, warn_msg)
                skipped += 1
                continue

            run_msg = f"\tRunning test " \
                f"{Fore.BLUE}{index}{Style.RESET_ALL}/"\
                f"{Fore.BLUE}{count}{Style.RESET_ALL} "\
                f"[{scene.name}]"
            print(run_msg, end=": ")

            self.clear_temp()
            cmd_params = self.prepare_command_line_params(scene)
            return_code = execute_process(cmd_params, self.env)
            result, msg = self.handle_result(return_code, scene.name)
            if not result:
                print(f"{Fore.RED}Failed!{Style.RESET_ALL}")
                print_error(f"\t{msg}")
                self.execution_results.add_result('failed', scene, msg)
                errors += 1
                continue
            success += 1
            print(f"{Fore.GREEN}Success{Style.RESET_ALL}")
            self.execution_results.add_result('success', scene, "success")

        end_msg = f'{Fore.MAGENTA}Tests completed{Style.RESET_ALL}:\n'\
            f'\tSucces: {Fore.GREEN}{success}{Style.RESET_ALL}/{count}\n'\
            f'\tFailed: {Fore.RED}{errors}{Style.RESET_ALL}/{count}\n'\
            f'\tSkipped:{Fore.YELLOW}{skipped}{Style.RESET_ALL}/{count}'
        print(end_msg)

        json_log = datetime.now().strftime(
            f'{self.params.program}_TEST_%Y-%m-%d_%H%M%S.json')
        self.execution_results.save(self.results_path / json_log)
        self.clear_temp()

    def prepare_command_line_params(self, scene: Scene) -> list:
        gpus = []
        for gpu_id in self.params.gpu[0].split(","):
            gpus.append("-gpu")
            gpus.append(gpu_id)

        cmd_params = [self.params.get_executable(), scene.path, "-oro",
                      "options.txt", "-oif", "png", "-oip", self.temp_output_path] + gpus
        if scene.skippostfx == 'true':
            cmd_params.append("-skippostfix")
        return cmd_params

    def handle_result(self, return_code: int, test_name: str) -> Tuple[bool, str]:
        log_file = get_latest_log_path() / "log.html"
        shutil.copy2(log_file, self.logs_path / f'{test_name}.result.html')

        # handle errors
        if return_code != 0:
            return False, "Process did not ended successfully!"

        result, msg = analyze_latest_log(log_file)
        if not result:
            return result, msg

        # handle output imgaes
        png_files = [Path(file_path)
                     for file_path in self.temp_output_path.glob('**/*.png')]
        for file_handler in png_files:
            # change output image name to test_name
            if len(file_handler.suffixes) > 1:
                name_parts = [test_name] + \
                    file_handler.suffixes[:-1] + [".result.png"]
            else:
                name_parts = [test_name] + [".result.png"]
            name = "".join(name_parts)
            dest_file = file_handler.with_name(name)
            file_handler.rename(dest_file)
            file_handler = dest_file
            shutil.copy2(file_handler, self.images_path)

        return True, "Success"

    def init_folders(self):
        results_folder_name = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        self.results_path = self.params.root_path / 'results' / results_folder_name

        self.temp_output_path = self.results_path / 'tmp'
        self.images_path = self.results_path/'images'
        self.logs_path = self.results_path/'logs'
        self.commons_path = self.results_path/'common'

        self.temp_output_path.mkdir(parents=True, exist_ok=True)
        self.images_path.mkdir(parents=True)
        self.logs_path.mkdir(parents=True)
        self.commons_path.mkdir(parents=True)

    def clear_temp(self):
        shutil.rmtree(self.temp_output_path)
        Path.mkdir(self.temp_output_path, parents=True)


class RedshiftCmdLineReferenceTask(Task):
    def __init__(self, params: ExecutionParameters):
        super().__init__(params)
        self.init_folders()
        self.execution_results = ExecutionResults()
        self.env['REDSHIFT_PATHOVERRIDE_STRING'] = self.params.user_config['required']['redshift_project_root']
        msg = f'{Fore.MAGENTA}Generating references for'\
              f'{Fore.GREEN} {params.program}{Style.RESET_ALL}'
        print(msg)

    def execute(self):
        count = len(self.scenes)
        success = 0
        skipped = 0
        errors = 0
        index = 0
        for scene_params in self.scenes:
            index += 1
            scene = Scene(scene_params, self.params.root_path)
            if not scene.path.exists():
                err_msg = f"{scene.path} do not exists"
                print_error(err_msg)
                self.execution_results.add_result("failed", scene, err_msg)
                errors += 1
                continue
            if not scene.type == ".rs":
                warn_msg = f'{Fore.YELLOW}Warning: {Fore.GREEN}{scene.path}{Style.RESET_ALL} is not redshift scene'
                print(warn_msg)
                self.execution_results.add_result('skipped', scene, warn_msg)
                skipped += 1
                continue

            run_msg = f"\tRunning test " \
                f"{Fore.BLUE}{index}{Style.RESET_ALL}/"\
                f"{Fore.BLUE}{count}{Style.RESET_ALL} "\
                f"[{scene.name}]"
            print(run_msg, end=": ")

            self.clear_temp()
            cmd_params = self.prepare_command_line_params(scene)
            return_code = execute_process(cmd_params, self.env)
            result, msg = self.handle_result(return_code, scene.name)
            if not result:
                print(f"{Fore.RED}Failed!{Style.RESET_ALL}")
                print_error(f"\t{msg}")
                self.execution_results.add_result('failed', scene, msg)
                errors += 1
                continue
            success += 1
            print(f"{Fore.GREEN}Success{Style.RESET_ALL}")
            self.execution_results.add_result('success', scene, "success")

        end_msg = f'{Fore.MAGENTA}Generation completed{Style.RESET_ALL}:\n'\
            f'\tSucces: {Fore.GREEN}{success}{Style.RESET_ALL}/{count}\n'\
            f'\tFailed: {Fore.RED}{errors}{Style.RESET_ALL}/{count}\n'\
            f'\tSkipped:{Fore.YELLOW}{skipped}{Style.RESET_ALL}/{count}'
        print(end_msg)

        json_log = datetime.now().strftime(
            f'{self.params.program}_REFERENCE_%Y-%m-%d_%H%M%S.json')
        self.execution_results.save(self.results_path / json_log)

        self.clear_temp()

    def prepare_command_line_params(self, scene: Scene) -> list:
        gpus = []
        for gpu_id in self.params.gpu[0].split(","):
            gpus.append("-gpu")
            gpus.append(gpu_id)

        cmd_params = [self.params.get_executable(), scene.path, "-oro",
                      "options.txt", "-oif", "png", "-oip", self.temp_output_path] + gpus
        if scene.skippostfx == 'true':
            cmd_params.append("-skippostfix")
        return cmd_params

    def handle_result(self, return_code: int, test_name: str) -> Tuple[bool, str]:
        log_file = get_latest_log_path() / "log.html"
        shutil.copy2(log_file, self.logs_path / f'{test_name}.reference.html')

        if return_code != 0:
            return False, "Process did not ended successfully!"

        log_file = get_latest_log_path() / "log.html"
        result, msg = analyze_latest_log(log_file)
        if not result:
            return result, msg

        # handle output imgaes
        png_files = [Path(file_path)
                     for file_path in self.temp_output_path.glob('**/*.png')]
        for file_handler in png_files:
            # change output image name to test_name
            if len(file_handler.suffixes) > 1:
                name_parts = [test_name] + \
                    file_handler.suffixes[:-1] + [".reference.png"]
            else:
                name_parts = [test_name] + [".reference.png"]
            name = "".join(name_parts)
            dest_file = file_handler.with_name(name)
            file_handler.rename(dest_file)
            file_handler = dest_file
            shutil.copy2(file_handler, self.images_path)
        return True, "Success"

    def init_folders(self):
        self.results_path = self.params.root_path / 'references' / 'redshiftCmdLine'
        self.temp_output_path = self.results_path / 'tmp'
        self.images_path = self.results_path/'images'
        self.logs_path = self.results_path/'logs'
        self.commons_path = self.results_path/'common'

        if self.params.no_delete:
            self.temp_output_path.mkdir(parents=True, exist_ok=True)
            self.images_path.mkdir(parents=True, exist_ok=True)
            self.logs_path.mkdir(parents=True, exist_ok=True)
            self.commons_path.mkdir(parents=True, exist_ok=True)
        else:
            shutil.rmtree(self.results_path)
            self.temp_output_path.mkdir(parents=True)
            self.images_path.mkdir(parents=True)
            self.logs_path.mkdir(parents=True)
            self.commons_path.mkdir(parents=True)

    def clear_temp(self):
        shutil.rmtree(self.temp_output_path)
        Path.mkdir(self.temp_output_path, parents=True)


class RedshiftBenchmarkTask(Task):
    def __init__(self, params: ExecutionParameters):
        super().__init__(params)
        self.init_folders()
        self.execution_results = ExecutionResults()
        self.env['REDSHIFT_PATHOVERRIDE_STRING'] = self.params.user_config['required']['redshift_project_root']
        msg = f'{Fore.MAGENTA}Executing test for'\
              f'{Fore.GREEN} {params.program}{Style.RESET_ALL}'
        print(msg)
    
    def execute(self):
        count = len(self.scenes)
        success = 0
        skipped = 0
        errors = 0
        index = 0
        for scene_params in self.scenes:
            index += 1
            scene = Scene(scene_params, self.params.root_path)
            if not scene.path.exists():
                err_msg = f"{scene.path} do not exists"
                print_error(err_msg)
                self.execution_results.add_result("failed", scene, err_msg)
                errors += 1
                continue
            if not scene.type == ".rs":
                warn_msg = f'{Fore.YELLOW}Warning: {Fore.GREEN}{scene.path}{Style.RESET_ALL} is not redshift scene'
                print(warn_msg)
                self.execution_results.add_result('skipped', scene, warn_msg)
                skipped += 1
                continue

            run_msg = f"\tRunning test " \
                f"{Fore.BLUE}{index}{Style.RESET_ALL}/"\
                f"{Fore.BLUE}{count}{Style.RESET_ALL} "\
                f"[{scene.name}]"
            print(run_msg, end=": ")

            self.clear_temp()
            cmd_params = self.prepare_command_line_params(scene)
            return_code = execute_process(cmd_params, self.env)
            result, msg = self.handle_result(return_code, scene.name)
            if not result:
                print(f"{Fore.RED}Failed!{Style.RESET_ALL}")
                print_error(f"\t{msg}")
                self.execution_results.add_result('failed', scene, msg)
                errors += 1
                continue
            success += 1
            print(f"{Fore.GREEN}Success{Style.RESET_ALL}")
            self.execution_results.add_result('success', scene, "success")

        end_msg = f'{Fore.MAGENTA}Generation completed{Style.RESET_ALL}:\n'\
            f'\tSucces: {Fore.GREEN}{success}{Style.RESET_ALL}/{count}\n'\
            f'\tFailed: {Fore.RED}{errors}{Style.RESET_ALL}/{count}\n'\
            f'\tSkipped:{Fore.YELLOW}{skipped}{Style.RESET_ALL}/{count}'
        print(end_msg)

        json_log = datetime.now().strftime(
            f'{self.params.program}_RESULT_%Y-%m-%d_%H%M%S.json')
        self.execution_results.save(self.results_path / json_log)

        self.clear_temp()

    def prepare_command_line_params(self, scene: Scene) -> list:
        gpus = []
        for gpu_id in self.params.gpu[0].split(","):
            gpus.append("-gpu")
            gpus.append(gpu_id)

        cmd_params = [self.params.get_executable(), scene.path] + gpus
        return cmd_params

    def handle_result(self, return_code: int, test_name: str) -> Tuple[bool, str]:
        log_file = get_latest_log_path() / "log.html"
        shutil.copy2(log_file, self.logs_path / f'{test_name}.result.html')

        if return_code != 0:
            return False, "Process did not ended successfully!"

        log_file = get_latest_log_path() / "log.html"
        result, msg = analyze_latest_log(log_file)
        if not result:
            return result, msg

        # handle output imgaes
        if get_os_tag() == "win":
            png_file = self.params.root_path / 'redshiftBenchmarkOutput.png'
        else:
            png_file = Path.home() / 'redshiftBenchmarkOutput.png'
        
        if not png_file.exists():
            return False, f"{png_file} does bit exists"

        # change output image name to test_name
        if len(png_file.suffixes) > 1:
            name_parts = [test_name] + \
                png_file.suffixes[:-1] + [".result.png"]
        else:
            name_parts = [test_name] + [".result.png"]
        name = "".join(name_parts)
        dest_file = png_file.with_name(name)
        if dest_file.exists():
            os.remove(dest_file)
        png_file.rename(dest_file)
        png_file = dest_file
        if Path.exists(self.images_path / name):
            os.remove(self.images_path / name)
        shutil.move(str(png_file), self.images_path)
        return True, "Success"

    def init_folders(self):
        results_folder_name = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        self.results_path = self.params.root_path / 'results' / results_folder_name

        self.temp_output_path = self.results_path / 'tmp'
        self.images_path = self.results_path/'images'
        self.logs_path = self.results_path/'logs'
        self.commons_path = self.results_path/'common'

        self.temp_output_path.mkdir(parents=True, exist_ok=True)
        self.images_path.mkdir(parents=True)
        self.logs_path.mkdir(parents=True)
        self.commons_path.mkdir(parents=True)

    def clear_temp(self):
        shutil.rmtree(self.temp_output_path)
        Path.mkdir(self.temp_output_path, parents=True)


class RedshiftBenchmarkReferenceTask(Task):
    def __init__(self, params: ExecutionParameters):
        super().__init__(params)
        self.init_folders()
        self.execution_results = ExecutionResults()
        self.env['REDSHIFT_PATHOVERRIDE_STRING'] = self.params.user_config['required']['redshift_project_root']
        msg = f'{Fore.MAGENTA}Generating references for'\
              f'{Fore.GREEN} {params.program}{Style.RESET_ALL}'
        print(msg)
    
    def execute(self):
        count = len(self.scenes)
        success = 0
        skipped = 0
        errors = 0
        index = 0
        for scene_params in self.scenes:
            index += 1
            scene = Scene(scene_params, self.params.root_path)
            if not scene.path.exists():
                err_msg = f"{scene.path} do not exists"
                print_error(err_msg)
                self.execution_results.add_result("failed", scene, err_msg)
                errors += 1
                continue
            if not scene.type == ".rs":
                warn_msg = f'{Fore.YELLOW}Warning: {Fore.GREEN}{scene.path}{Style.RESET_ALL} is not redshift scene'
                print(warn_msg)
                self.execution_results.add_result('skipped', scene, warn_msg)
                skipped += 1
                continue

            run_msg = f"\tRunning test " \
                f"{Fore.BLUE}{index}{Style.RESET_ALL}/"\
                f"{Fore.BLUE}{count}{Style.RESET_ALL} "\
                f"[{scene.name}]"
            print(run_msg, end=": ")

            self.clear_temp()
            cmd_params = self.prepare_command_line_params(scene)
            return_code = execute_process(cmd_params, self.env)
            result, msg = self.handle_result(return_code, scene.name)
            if not result:
                print(f"{Fore.RED}Failed!{Style.RESET_ALL}")
                print_error(f"\t{msg}")
                self.execution_results.add_result('failed', scene, msg)
                errors += 1
                continue
            success += 1
            print(f"{Fore.GREEN}Success{Style.RESET_ALL}")
            self.execution_results.add_result('success', scene, "success")

        end_msg = f'{Fore.MAGENTA}Generation completed{Style.RESET_ALL}:\n'\
            f'\tSucces: {Fore.GREEN}{success}{Style.RESET_ALL}/{count}\n'\
            f'\tFailed: {Fore.RED}{errors}{Style.RESET_ALL}/{count}\n'\
            f'\tSkipped:{Fore.YELLOW}{skipped}{Style.RESET_ALL}/{count}'
        print(end_msg)

        json_log = datetime.now().strftime(
            f'{self.params.program}_REFERENCE_%Y-%m-%d_%H%M%S.json')
        self.execution_results.save(self.results_path / json_log)

        self.clear_temp()

    def prepare_command_line_params(self, scene: Scene) -> list:
        gpus = []
        for gpu_id in self.params.gpu[0].split(","):
            gpus.append("-gpu")
            gpus.append(gpu_id)

        cmd_params = [self.params.get_executable(), scene.path] + gpus
        return cmd_params

    def handle_result(self, return_code: int, test_name: str) -> Tuple[bool, str]:
        log_file = get_latest_log_path() / "log.html"
        shutil.copy2(log_file, self.logs_path / f'{test_name}.reference.html')

        if return_code != 0:
            return False, "Process did not ended successfully!"

        log_file = get_latest_log_path() / "log.html"
        result, msg = analyze_latest_log(log_file)
        if not result:
            return result, msg

        # handle output imgaes
        if get_os_tag() == "win":
            png_file = self.params.root_path / 'redshiftBenchmarkOutput.png'
        else:
            png_file = Path.home() / 'redshiftBenchmarkOutput.png'
        
        if not png_file.exists():
            return False, f"{png_file} does bit exists"

        # change output image name to test_name
        if len(png_file.suffixes) > 1:
            name_parts = [test_name] + \
                png_file.suffixes[:-1] + [".reference.png"]
        else:
            name_parts = [test_name] + [".reference.png"]
        name = "".join(name_parts)
        dest_file = png_file.with_name(name)
        if dest_file.exists():
            os.remove(dest_file)
        png_file.rename(dest_file)
        png_file = dest_file
        if Path.exists(self.images_path / name):
            os.remove(self.images_path / name)
        shutil.move(str(png_file), self.images_path)
        return True, "Success"
                    
        return True, "Success"

    def init_folders(self):
        results_folder_name = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        self.results_path = self.params.root_path / 'references' / 'redshiftBenchmark'

        self.temp_output_path = self.results_path / 'tmp'
        self.images_path = self.results_path/'images'
        self.logs_path = self.results_path/'logs'
        self.commons_path = self.results_path/'common'

        self.temp_output_path.mkdir(parents=True, exist_ok=True)
        self.images_path.mkdir(parents=True, exist_ok=True)
        self.logs_path.mkdir(parents=True,exist_ok=True)
        self.commons_path.mkdir(parents=True,exist_ok=True)

    def clear_temp(self):
        shutil.rmtree(self.temp_output_path)
        Path.mkdir(self.temp_output_path, parents=True)


class TaskFactory:
    def create_task(self, params: ExecutionParameters) -> Task:
        if params.reference:
            if params.program == 'redshiftBenchmark':
                return RedshiftBenchmarkReferenceTask(params)
            elif params.program == 'redshiftCmdLine':
                return RedshiftCmdLineReferenceTask(params)
            else:
                raise ValueError("Invalid or not supported task type")
        else:
            if params.program == 'redshiftBenchmark':
                return RedshiftBenchmarkTask(params)
            elif params.program == 'redshiftCmdLine':
                return RedshiftCmdLineTask(params)
            else:
                raise ValueError("Invalid or not supported task type")



@dataclass
class AnalysisItem:
    def __init__(self, reference_image: Path, result_image: Path, name: str, plot_path: Path, treshold: float = 0.95, crop:bool=False):
        self.reference_image = reference_image
        self.result_image = result_image
        self.name = name
        self.treshold = treshold
        self.output_dir = plot_path
        self.crop = crop
        self.mse = 0.0
        self.ssi = 0.0

    # cuts the bottom part of the image description generatedby the benchmark    
    def _trim(self, imdata):
        known_heights = {
                256 : 163,
                360 : 267,
                341 : 254,
                431 : 238,
                450 : 357,
                480 : 387,
                486 : 393,
                512 : 420,
                540 : 447,
                562 : 470,
                576 : 484, 
                600 : 507,
                720 : 628,
                729 : 637,
                800: 708,
                900: 808,
                1024: 932,
                1075: 983,
                1080: 988,
                1125: 1032,
                1500: 1408,
                1800: 1708,
                2048: 1956,
                4500: 4408
        }
        w, h, n = imdata.shape
        return imdata[0: known_heights[w], :, :]

    def compute_mse_and_ssi(self) -> None:
        if self.crop:
            cv_ref = self._trim(cv.imread(str(self.reference_image)))
            cv_res = self._trim(cv.imread(str(self.result_image)))
        else:
            cv_ref = cv.imread(str(self.reference_image))
            cv_res = cv.imread(str(self.result_image))

        cv_ref = cv.cvtColor(cv_ref, cv.COLOR_BGR2GRAY)
        cv_res = cv.cvtColor(cv_res, cv.COLOR_BGR2GRAY)

        self.mse = mean_squared_error(cv_ref, cv_res)
        #it make sense to ocmpare images that are not enitrely black
        data_range = cv_ref.max() - cv_ref.min()
        self.ssi = 1.0
        if data_range > 0:
            self.ssi = ssim(cv_ref, cv_res, data_range=data_range)

    def create_diff_plot(self, plot_file_path:Path)->None:
        if self.crop:
            cv_ref = self._trim(cv.imread(str(self.reference_image)))
            cv_res = self._trim(cv.imread(str(self.result_image)))
        else:
            cv_ref = cv.imread(str(self.reference_image))
            cv_res = cv.imread(str(self.result_image))
        
        fig, axes = plt.subplots(ncols=3, figsize=(19.20,10.80), sharex=True, sharey=True)
        ax = axes.ravel()

        diff = 255 - cv.absdiff(cv_ref, cv_res)
        ax[0].imshow(cv_ref, cmap=plt.cm.gray, vmin=0, vmax=255)
        ax[0].set_title('Original image')

        ax[1].imshow(diff, cmap=plt.cm.gray, vmin=0, vmax=255)
        ax[1].set_xlabel('')
        ax[1].set_title('Diff Image')

        ax[2].imshow(cv_res, cmap=plt.cm.gray, vmin=0, vmax=255)
        ax[2].set_xlabel(f'MSE: {self.mse:.2f}, SSIM: {self.ssi:.2f}')
        ax[2].set_title('Result Image')

        plt.tight_layout()
        plt.savefig(str(plot_file_path), dpi=300)
        plt.close()

def Analyze(item: AnalysisItem):
    try:
        item.compute_mse_and_ssi()
        if item.mse > item.treshold:
            shutil.copy2(item.reference_image, item.output_dir)
            shutil.copy2(item.result_image, item.output_dir)
            plot_file = item.output_dir / f'{item.name}.diff.png'
            item.create_diff_plot(plot_file)
    except ValueError as ve:
        print_error(f"Analysis of {item.name} failed: {repr(ve)}")
    return item
    

class ImageAnalyzer:
    def __init__(self, references_path: Path, results_path: Path, treshold: float = 0.95, crop: bool = False):
        self.reference_path = references_path
        self.results_path = results_path
        self.analysis_output_path = self.results_path / 'common'
        self._validate_path(self.reference_path)
        self._validate_path(self.results_path)
        self._validate_path(self.analysis_output_path)
        shutil.rmtree(self.analysis_output_path)
        self.analysis_output_path.mkdir(parents=True)

        self.treshold = treshold
        self.crop = crop
        self.analysis_items = []
        self.mismatch_items = []


    def analyze(self):
        print(f'{Fore.MAGENTA}Analyzing results {Style.RESET_ALL}')
        all, found, missing = self.match_results_with_references()
        if missing:
            warn_msg = f'{Fore.YELLOW}Warning: Not all references found.\n{Style.RESET_ALL}' \
                       f'\tNumber of all result images {Fore.BLUE}{all}{Style.RESET_ALL}\n' \
                       f'\tNumber of reference images {Fore.BLUE}{found}{Style.RESET_ALL}'
            print(warn_msg)
            print("Missing files: ")
            for f in missing:
                info = f'\t{Fore.YELLOW}{f}{Style.RESET_ALL}'
                print(info)

        if not found:
            print("There is nothing to compare")
            return
        
        analyzed_items = []    
        if USE_MULTIPROCESSING:
            with Pool() as pool:
                # Meh... the analysisItem is not the best one
                results = pool.imap_unordered(Analyze, self.analysis_items)
                for item in results:
                    msg = f"Analysis of {Fore.GREEN}{item.name}{Style.RESET_ALL}: " \
                        f"mse={Fore.BLUE}{item.mse:.3f}{Style.RESET_ALL}, "\
                        f"ssi={Fore.BLUE}{item.ssi:.3f}{Style.RESET_ALL}"
                    print(msg)
                    analyzed_items.append(item)
        else:
            analyzed_items =[Analyze(item) for item in self.analysis_items]            
            for item in analyzed_items:
                msg = f"Analysis of {Fore.GREEN}{item.name}{Style.RESET_ALL}: " \
                      f"mse={Fore.BLUE}{item.mse:.3f}{Style.RESET_ALL}, "\
                      f"ssi={Fore.BLUE}{item.ssi:.3f}{Style.RESET_ALL}"
                print(msg)
        
        self.analysis_items = analyzed_items
                
        mismatch_images = [ item for item in self.analysis_items if item.mse > item.treshold]
        mismatch_images.sort(key=lambda x: x.mse, reverse=True)
        msg = f"\n{Fore.YELLOW}There are {Fore.BLUE}{len(mismatch_images)}{Fore.YELLOW} that requires inspection{Style.RESET_ALL}"
        print(msg)
        for item in mismatch_images:
            print(f"\tmse={Fore.BLUE}{item.mse:.3f}{Style.RESET_ALL} [{Fore.GREEN}{item.name}{Style.RESET_ALL}]")
        self.mismatch_items = mismatch_images

    def save_data(self, file: Path, data):
        json_data = json.dumps(data, indent=2)
        try:
            with open(file, 'w') as json_file:
                json_file.write(json_data)
        except IOError as io_err:
            print_error(
                f"Could not save analysis info to {file} [{repr(io_err)}]")

    def save(self, file:Path):
        results = {item.name: {"mse": item.mse, "ssi": item.ssi} for item in self.analysis_items}
        self.save_data(file, results)
    
    def save_mismatch(self, file:Path):
        results = {item.name: {"mse": item.mse, "ssi": item.ssi} for item in self.mismatch_items}
        if results:
            self.save_data(file, results)
        else:
            print("No mismatch image information found to save.")
        
    #returns total number of image to analyze and number of matches found
    def match_results_with_references(self) -> Tuple[int, int, list]:
        # scan the results path and collect all images
        # for each image from results find a reference image
        missing_items = []
        result_image_paths = [Path(f) for f in self.results_path.glob('**/*.png')]
        self.to_compare_items = []
        for result_image in result_image_paths:
            result_name_parts = result_image.name.split(".")
            # name except 'result.png'
            base_name = ".".join(result_name_parts[:-2])

            reference_name_parts = result_name_parts[:-2] + \
                                   ["reference", "png"]
            
            reference_name =  ".".join(reference_name_parts)
            reference_file = self.reference_path / 'images' / reference_name

            if not reference_file.exists():
                warn_msg = f'{Fore.YELLOW}Warning: {Fore.GREEN}{reference_file}{Style.RESET_ALL} does not exists'
                missing_items.append(reference_file)
                print(warn_msg)
                continue
            self.analysis_items.append(AnalysisItem(reference_file, result_image, base_name, self.analysis_output_path, self.treshold, self.crop))

        number_of_all_items = len(result_image_paths)
        number_of_matcehd_items = len(self.analysis_items)
        return number_of_all_items, number_of_matcehd_items, missing_items

    def _validate_path(self, path:Path):
        if not path.exists():
            msg = f'[Image Analyzer] path does not exists {path}'
            print_error(msg)
            raise IOError(msg)
       




#************************************
#          MAIN
#************************************

if __name__ == "__main__":
    color_terminal.init()
    print(f"{Fore.BLUE}Redshift Unit Tests{Style.RESET_ALL}")

    try:
        execution_parameters = parse_command_line_args()
        is_valid, reason = execution_parameters.validate()

        if not is_valid:
            print_error(f'{reason}')
            exit(EXIT_FAILURE)
        pprint.pprint(execution_parameters.__dict__, indent=2)
    except IOError as io_error:
        print_error(repr(io_error))
        exit(EXIT_FAILURE)
    except ValueError as val_error:
        print_error(repr(val_error))
        exit(EXIT_FAILURE)

    factory = TaskFactory()
    task = factory.create_task(execution_parameters)
    task.execute()

    # schedule results analysis for the task that was not a reference generation
    if not task.params.reference:
        crop = task.params.program == "redshiftBenchmark"
        analyzer = ImageAnalyzer(task.reference_path / task.params.program, task.results_path, task.params.treshold, crop)
        analyzer.analyze()
        analysis_log = datetime.now().strftime(
            f'{task.params.program}_ANALYSIS_%Y-%m-%d_%H%M%S.json')
        mismatch_log = datetime.now().strftime(
            f'{task.params.program}_ANALYSIS_MISMACH_%Y-%m-%d_%H%M%S.json')
        analyzer.save(task.results_path / analysis_log)
        analyzer.save_mismatch(task.results_path / mismatch_log)


    print(f"\n{Fore.BLUE}Redshift Unit Tests Finished{Style.RESET_ALL}")


   
    
