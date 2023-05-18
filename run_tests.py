import platform
from typing import Tuple
import re as re
import colorama
import argparse
import pprint
import json
from pathlib import Path
import os
import datetime
from subprocess import Popen, PIPE
import shutil as shutil
import json
from dataclasses import dataclass, asdict

EXIT_SUCCESS = 0
EXIT_FAILURE = -1

def print_error(msg:str):
    print(f'{colorama.Fore.RED}ERROR: {msg}{colorama.Style.RESET_ALL}')

def print_msg(msg:str, color = None):
    if color:
        print(f"{color}{msg}{colorama.Style.RESET_ALL}")
    else:
        print(f"{msg}")

def get_os_tag() -> str:
    if platform.system() == "Linux":
        return "linux"
    elif platform.system() == "Windows":
        return "win"
    else:
        raise RuntimeError("{} is not supported".format(platform.system()))

@dataclass(init=False, eq=False, order=False, repr=True)
class ExecutionParameters:
    def __init__(self, args):
        self.reference = args.reference
        self.tests = args.test
        self.no_delete =args.no_delete
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

    #def __repr__(self) -> str:
    #    return f"program: {self.program}, test: {self.tests}, gpu: {self.gpu}, reference: {self.reference} treshold: {self.treshold}, no_delete: {self.no_delete}" 
    
    def print_config(self) -> None:
        pp = pprint.PrettyPrinter(indent=4)
        pp.pprint(self.config)

    def print_user_config(self) -> None:
        pp = pprint.PrettyPrinter(indent=4)
        pp.pprint(self.user_config)
   
    def validate(self) -> (bool, str):
        #config
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
    parser.add_argument('--reference', action='store_true')
    parser.add_argument('--config', default=DEFAULT_CONFIG_FILE)
    parser.add_argument('--user-config', default=DEFAULT_USER_CONFIG_FILE)
    parser.add_argument('--test', action='append', required=True)
    parser.add_argument('--no-delete', action='store_true')
    parser.add_argument('--gpu', nargs='+', action='extend')
    parser.add_argument("--treshold", type=float, default=0.95)
    parser.add_argument("--program", choices=['redshiftCmdLine', 'redshiftBenchmark', 'maya'], help='Choose program to execute the tests')

    args = parser.parse_args()
    parameters = ExecutionParameters(args)
    return parameters

def process_test_files(test_json, tests_list) -> None:
    try:
        with open(test_json, 'r') as test_file:
            conf = json.load(test_file)
        tests = conf['tests']
        for t in tests:
            if 'include' in t:
                process_test_files(Path(test_json).parent / t['include'], tests_list)
            else:
                tests_list.append(t)
    except IOError as e:
        raise e

def load_test_files(tests:list) -> list:
    print(f'{colorama.Fore.GREEN}Processing files from{colorama.Style.RESET_ALL}: {tests}')
    scene_files = []
    try:
        for json_test in tests:
            process_test_files(json_test, scene_files)
    except IOError as io_err:
          print_error(repr(io_err))
          exit(EXIT_FAILURE)   
    print(f'{colorama.Fore.BLUE}{len(scene_files)}{colorama.Style.RESET_ALL} tests were found')
    return scene_files

def execute_process(params:list, user_env=None) -> int:
    params_str = [str(p) for p in params]
    process=Popen(params_str, env=user_env, stdout=PIPE, stderr=PIPE, shell=False)
    stdout, stderr = process.communicate()
    return process.returncode

def get_latest_log_path() -> Path:
    if get_os_tag() == "win":
        return Path("C:\\ProgramData\\Redshift\\Log\\Log.Latest.0")
    else:
        return Path.home() / 'redshift/log/log.latest.0'

def analyze_latest_log(log_file: Path) -> Tuple[bool, str]:
    error_message='Redshift encountered an unrecoverable error during rendering and has been disabled.'
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

@dataclass(repr=True)
class Scene:
    def __init__(self, param:dict, root_path:Path):
        self.name = param["test_name"]
        self.path = root_path / 'scenes' / Path(param["path_to_scene"])
        self.type = self.path.suffix
        self.frames = ('1','1') if not "frames" in param else param["frames"]
        self.skippostfx = "false" if not "skippostfx" in param else param["skippostfx"]

'''
Keeps the information about the scenes
that Succeeded, Failed or skipped
'''
class ExecutionResults:
    def __init__(self):
        self.info = {
            "summary" : {}, #dict
            "success" : [],
            "failed" : [],
            "skipped" : []
        }

    def add_result(self, type: str, scene:Scene, err_msg:str = "") -> None:
        self.info[type].append((scene.name, str(scene.path), err_msg))

    def _summary(self) -> None:
        summary = {key: len(value) for key, value in self.info.items() if key != 'summary'}
        self.info["summary"] = summary
        
    def save(self, file: Path) -> None:
        self._summary()
        json_data = json.dumps(self.info, indent=2)
        try:
            with open(file, 'w') as json_file:
                json_file.write(json_data)
        except IOError as io_err:
            print_error(f"Could not save execution info to {file} [{repr(io_err)}]")


class Task:
    def __init__(self, params:ExecutionParameters):
        self.params = params
        self.scenes = load_test_files(params.tests)
        self.env = os.environ.copy()
        self.reference_results_path = params.root_path / 'reference'
   
    def execute(self):
        pass

    def handle_result(self):
        pass

    def init_folders(self):
        pass
       

class RedshiftCmdLineTask(Task):
    def __init__(self, params:ExecutionParameters):
        super().__init__(params)
        self.init_folders()
        self.execution_results = ExecutionResults()
        self.env['REDSHIFT_PATHOVERRIDE_STRING'] = self.params.user_config['required']['redshift_project_root']
        msg = f'{colorama.Fore.MAGENTA}Executing test for'\
              f'{colorama.Fore.GREEN} {params.program}{colorama.Style.RESET_ALL}'
        print(msg)


    def execute(self):
        count = len(self.scenes)
        success =0
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
                warn_msg = f'{colorama.Fore.YELLOW}Warning: {colorama.Fore.GREEN}{scene.path}{colorama.Style.RESET_ALL} is not redshift scene'
                print(warn_msg)
                self.execution_results.add_result('skipped', scene, warn_msg)
                skipped += 1
                continue
            
            run_msg = f"\tRunning test " \
                 f"{colorama.Fore.BLUE}{index}{colorama.Style.RESET_ALL}/"\
                 f"{colorama.Fore.BLUE}{count}{colorama.Style.RESET_ALL} "\
                 f"[{scene.name}]"
            print(run_msg, end = ": ")

            self.clear_temp()
            cmd_params = self.prepare_command_line_params(scene)
            return_code = execute_process(cmd_params, self.env)
            result, msg = self.handle_result(return_code, scene.name)
            if not result:
                print(f"{colorama.Fore.RED}Failed!{colorama.Style.RESET_ALL}")    
                print_error(f"\t{msg}")
                self.execution_results.add_result('failed', scene, msg)
                errors+=1
                continue
            success+=1
            print(f"{colorama.Fore.GREEN}Success{colorama.Style.RESET_ALL}")
            self.execution_results.add_result('success', scene, "success")
        
        end_msg = f'{colorama.Fore.MAGENTA}Tests completed{colorama.Style.RESET_ALL}:\n'\
              f'\tSucces: {colorama.Fore.GREEN}{success}{colorama.Style.RESET_ALL}/{count}\n'\
              f'\tFailed: {colorama.Fore.RED}{errors}{colorama.Style.RESET_ALL}/{count}\n'\
              f'\tSkipped:{colorama.Fore.YELLOW}{skipped}{colorama.Style.RESET_ALL}/{count}'
        print(end_msg)

        json_log = datetime.datetime.now().strftime(f'{self.params.program}_%Y-%m-%d_%H%M%S.json')
        self.execution_results.save(self.params.root_path / json_log)

        
        self.clear_temp()


    def prepare_command_line_params(self, scene: Scene) -> list:
        gpus = []
        for gpu_id in self.params.gpu[0].split(","):
            gpus.append("-gpu")
            gpus.append(gpu_id)

        cmd_params = [self.params.get_executable(), scene.path, "-oro", "options.txt", "-oif", "png", "-oip", self.temp_output_path] + gpus
        if scene.skippostfx == 'true':
            cmd_params.append("-skippostfix")
        return cmd_params
   
    def handle_result(self, return_code:int, test_name:str) -> Tuple[bool, str]:
        #handle errors
        if return_code != 0:
            return False, "Process did not ended successfully!"
        
        log_file = get_latest_log_path() / "log.html"
        result, msg = analyze_latest_log(log_file)
        if not result:
            return result, msg
        shutil.copy2(log_file, self.logs_path / f'{test_name}.html')

        #handle output imgaes
        png_files = [Path(file_path) for file_path in self.temp_output_path.glob('**/*.png')]
        for file_handler in png_files:
            # change output image name to test_name 
            new_name = file_handler.with_name(test_name).with_suffix('.result.png')
            file_handler.rename(new_name)
            file_handler = new_name
            shutil.copy2(file_handler, self.images_path)
        
        return True, "Success"
    
    def clear_temp(self):
       shutil.rmtree(self.temp_output_path)
       self.temp_output_path.mkdir(parents=True, exist_ok=True)

    def init_folders(self):
        results_folder_name=datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')       
        self.results_path = self.params.root_path / 'results' / results_folder_name

        self.temp_output_path = self.results_path / 'tmp'
        self.images_path = self.results_path/'images'
        self.logs_path = self.results_path/'logs'
        self.commons_path = self.results_path/'common'

        self.temp_output_path.mkdir(parents=True, exist_ok=True)
        self.images_path.mkdir(parents=True)
        self.logs_path.mkdir(parents=True)
        self.commons_path.mkdir(parents=True)
        
class RedshiftCmdLineReferenceTask(Task):
    pass

class RedshiftBenchmarkTask(Task):
    pass

class RedshiftBenchmarkReferenceTask(Task):
    pass

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
           
   

       

# def generate_references(params:ExecutionParameters):
#     msg = f'{colorama.Fore.MAGENTA}Generating references for'\
#           f'{colorama.Fore.GREEN} {execution_parameters.program}{colorama.Fore.MAGENTA}'\
#           f' with no_delete: {colorama.Fore.GREEN}{execution_parameters.no_delete}{colorama.Style.RESET_ALL}'
#     print(msg)   

# def run_benchmark(params:ExecutionParameters):
#     msg = f'{colorama.Fore.MAGENTA}Executing test for'\
#           f'{colorama.Fore.GREEN} {execution_parameters.program}{colorama.Style.RESET_ALL}'
#     print(msg)

#     scenes = load_test_files(params.tests)

# def run_cmdline(params:ExecutionParameters):
#     msg = f'{colorama.Fore.MAGENTA}Executing test for'\
#           f'{colorama.Fore.GREEN} {execution_parameters.program}{colorama.Style.RESET_ALL}'
#     print(msg)
#     scenes = load_test_files(params.tests)
    

if __name__ == "__main__":
    colorama.init()
    print(f"{colorama.Fore.BLUE}Redshift Unit Tests{colorama.Style.RESET_ALL}")

    try:
        execution_parameters = parse_command_line_args()
        is_valid, reason = execution_parameters.validate()
    
        if not is_valid:
            print_error(f'{reason}')
            exit(EXIT_FAILURE) 
        pprint.pprint(execution_parameters.__dict__, indent= 2)
    except IOError as io_error:
        print_error(repr(io_error))
        exit(EXIT_FAILURE)
    except ValueError as val_error:
        print_error(repr(val_error))
        exit(EXIT_FAILURE)

    
    factory = TaskFactory()
    task = factory.create_task(execution_parameters)
    task.execute()

    