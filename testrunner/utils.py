import argparse
import json
import platform
import pprint as pprint
import re as re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from pprint import PrettyPrinter
from subprocess import PIPE, Popen
from typing import List, Tuple

from colorama import Fore, Style

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


def validate_path(path:Path):
    if not path.exists():
        msg = f'Path does not exists {path}'
        print_error(msg)
        raise IOError(msg)
    
def read_html(file: Path):
    with open(file, 'r') as f:
        content = f.read()
    return content

def date_time_with_prefix(prefix:str, ext:str="json", format:str='%Y-%m-%d_%H%M%S'):
    if not prefix:
        return datetime.now().strftime('%Y-%m-%d_%H%M%S')
    else:
        return datetime.now().strftime(f'{prefix}_%Y-%m-%d_%H%M%S.{ext}')

def convert_to_seconds(time_string:str, time_format="%Hh:%Mm:%Ss"):
    time_object = datetime.strptime(time_string, time_format)
    total_seconds = timedelta(hours=time_object.hour, minutes=time_object.minute, seconds=time_object.second).total_seconds()
    return total_seconds


def split_to_gpus(val:str, prefix= "-gpu", sep:str=",") -> List[str]:
    items = []
    for item in val.split(sep):
            items.append(prefix)
            items.append(item)
    return items
    

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


@dataclass(init=False, eq=False, order=False, repr=True)
class ExecutionParameters:

    reference: bool
    no_delete: bool
    treshold: float
    root_path: Path
    analysis_path: Path
    performance_analysis: bool
    image_analysis: bool
    gpu: List[str]
    program: str
    test: str

    def __init__(self, args):
        self.reference = args.reference
        self.tests = args.test
        self.no_delete = args.no_delete
        self.treshold = args.treshold
        self.program = args.program
        self.gpu = args.gpu
        self.root_path = Path("./").resolve()
        self.performance_analysis = args.performance_analysis
        self.image_analysis = args.image_analysis
        self.analysis_path = Path(str(args.analysis_path))

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
    parser.add_argument('--test', action='append', required=False, help='Test to execute')
    parser.add_argument('--no-delete', action='store_true', help='deprecated')
    parser.add_argument('--gpu', nargs='+', required=False, action='extend', help='GPU to execute the program. For multi-GPU separete with comma --gpu 1,2,3')
    parser.add_argument("--treshold", type=float, default=0.95, help="Mean Square Root [mse] value above which the image is considered incorrect")
    parser.add_argument("--program", choices=['redshiftCmdLine',
                        'redshiftBenchmark', 'maya'], required=True, help='Choose program to execute the tests')
    parser.add_argument("--performance-analysis", action="store_true", help="Extract the performance results from the --analysis_path")
    parser.add_argument("--image-analysis", action="store_true", help="Run the image analysis task on the results from --analysis_path")
    parser.add_argument("--analysis-path", type=str, help="Path to the results for analysis")

    args = parser.parse_args()
    parameters = ExecutionParameters(args)
    return parameters


