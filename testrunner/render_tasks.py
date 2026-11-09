

import json
import os as os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass

from pathlib import Path

from .utils import *


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


class RenderingTask(Task):
    
    results_folder_name: str
    results_path: Path
    result_suffix: str
    run_msg: str
    end_msg: str
    results_json_log = Path
    execution_results: ExecutionResults
    
    def __init__(self, params: ExecutionParameters):
        super().__init__(params)
        self.execution_results = ExecutionResults()
        self.env['REDSHIFT_PATHOVERRIDE_STRING'] = self.params.user_config['required']['redshift_project_root']
        print(f'{Fore.MAGENTA}Executing {Fore.GREEN}{params.program}{Style.RESET_ALL} [reference: {self.params.reference}]')

    def init_folders(self):
        self.temp_output_path = self.results_path / 'tmp'
        self.images_path = self.results_path/'images'
        self.logs_path = self.results_path/'logs'
        self.commons_path = self.results_path/'common'
   
        self.temp_output_path.mkdir(parents=True, exist_ok=True)
        self.images_path.mkdir(parents=True, exist_ok=True)
        self.logs_path.mkdir(parents=True, exist_ok=True)
        self.commons_path.mkdir(parents=True, exist_ok=True)
        
    def clear_temp(self):
        shutil.rmtree(self.temp_output_path)
        Path.mkdir(self.temp_output_path, parents=True)
    
    def handle_result(self, return_code: int, test_name: str) -> Tuple[bool, str]:
        log_file = get_latest_log_path() / "log.html"
        shutil.copy2(log_file, self.logs_path / f'{test_name}.{self.result_suffix}.html')

        if return_code != 0:
            result, msg = analyze_latest_log(log_file)
            if not result:
                return result, msg
            return False, "Process did not ended successfully!"
        
        if self.params.program == "redshiftBenchmark":
            if get_os_tag() == "win":
                output_image = self.params.root_path / 'redshiftBenchmarkOutput.png'
            else:
                output_image = Path.home() / 'redshiftBenchmarkOutput.png'       
            if not output_image.exists():
                return False, f"{output_image} does bit exists"
            self.rename_and_move_to_results(output_image, test_name)
        else:
            output_images = [Path(file_path)
                            for file_path in self.temp_output_path.glob('**/*.png')]
            for output_image in output_images:
                self.rename_and_move_to_results(output_image, test_name)
        return True, "Success"
    
    def rename_and_move_to_results(self, output_image:Path, name:str):
        # change output image name to test_name
        parts = output_image.name.split(".")
        _name = ".".join( [name] + parts[:-1] + [f"{self.result_suffix}.png"]) # parts[1:-1] select all in between main name and the trailing png.
        # _name = "".join([output_image.name, f"{self.result_suffix}.png"])
        suffixed_image_path = output_image.with_name(_name)
        if suffixed_image_path.exists():
            print(f"{Fore.YELLOW}Warning:{Style.RESET_ALL} removing [{suffixed_image_path}]")
            os.remove(suffixed_image_path)
        
        output_image = output_image.rename(suffixed_image_path)
        if Path.exists(self.images_path / _name):
            print(f"{Fore.YELLOW}Warning:{Style.RESET_ALL} removing [{self.images_path / _name}]")
            os.remove(self.images_path / _name)
        shutil.move(str(output_image), self.images_path)

    def prepare_command_line_params(self, scene: Scene) -> list:
        return [] 

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

            print(self.run_msg.format(color = Fore.BLUE, reset= Style.RESET_ALL, index=index, count=count, scene=scene.name), end=": ")

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

        print(self.end_msg.format(
            reset=Style.RESET_ALL, 
            magenta=Fore.MAGENTA, green=Fore.GREEN, red=Fore.RED, yellow=Fore.YELLOW,
            success=success, errors=errors, skipped=skipped, count=count))

        self.execution_results.save(self.results_json_log)
        self.clear_temp()


class RedshiftCmdLineTask(RenderingTask):
    def __init__(self, params: ExecutionParameters):
        super().__init__(params)
        date_name = date_time_with_prefix("")
        self.results_folder_name = date_name
        self.results_path = self.params.root_path / 'results' / self.results_folder_name
        self.result_suffix = "result"
        self.init_folders()
        self.run_msg = "\tRunning test {color}{index}{reset}/{color}{count}{reset} [{scene}]"
        self.end_msg = '{magenta}Tests completed{reset}:\n' \
            '\tSucces: {green}{success}{reset}/{count}\n' \
            '\tFailed: {red}{errors}{reset}/{count}\n' \
            '\tSkipped:{yellow}{skipped}{reset}/{count}'
        self.results_json_log = self.results_path / f'{self.params.program}_TEST_{date_name}.json'

    def prepare_command_line_params(self, scene: Scene) -> list:
        gpus =  split_to_gpus(self.params.gpu[0])
        cmd_params = [self.params.get_executable(), scene.path, "-oro",
                      "options.txt", "-oif", "png", "-oip", self.temp_output_path] + gpus
        if scene.skippostfx == 'true':
            cmd_params.append("-skippostfix")
        return cmd_params


class RedshiftCmdLineReferenceTask(RenderingTask):
    def __init__(self, params: ExecutionParameters):
        super().__init__(params)
        date_name = date_time_with_prefix("")
        self.results_folder_name = self.params.program
        self.results_path = self.params.root_path / 'references' / self.results_folder_name
        self.result_suffix = "reference"
        self.init_folders()
        self.run_msg = "\tGenerating {color}{index}{reset}/{color}{count}{reset} [{scene}]"
        self.end_msg = '{magenta}Generation completed{reset}:\n' \
            '\tSucces: {green}{success}{reset}/{count}\n' \
            '\tFailed: {red}{errors}{reset}/{count}\n' \
            '\tSkipped:{yellow}{skipped}{reset}/{count}'
        self.results_json_log = self.results_path / f'{self.params.program}_REFERENCE_{date_name}.json'

    def prepare_command_line_params(self, scene: Scene) -> list:
        gpus =  split_to_gpus(self.params.gpu[0])
        cmd_params = [self.params.get_executable(), scene.path, "-oro",
                      "options.txt", "-oif", "png", "-oip", self.temp_output_path] + gpus
        if scene.skippostfx == 'true':
            cmd_params.append("-skippostfix")
        return cmd_params


class RedshiftBenchmarkTask(RenderingTask):
    def __init__(self, params: ExecutionParameters):
        super().__init__(params)
        date_name = date_time_with_prefix("")
        self.results_folder_name = date_name
        self.results_path = self.params.root_path / 'results' / self.results_folder_name
        self.result_suffix = "result"
        self.init_folders()
        self.run_msg = "\tRunning test {color}{index}{reset}/{color}{count}{reset} [{scene}]"
        self.end_msg = '{magenta}Tests completed{reset}:\n' \
            '\tSucces: {green}{success}{reset}/{count}\n' \
            '\tFailed: {red}{errors}{reset}/{count}\n' \
            '\tSkipped:{yellow}{skipped}{reset}/{count}'
        self.results_json_log = self.results_path / f'{self.params.program}_TEST_{date_name}.json'

  
    def prepare_command_line_params(self, scene: Scene) -> list:
        gpus =  split_to_gpus(self.params.gpu[0])
        cmd_params = [self.params.get_executable(), scene.path] + gpus
        return cmd_params


class RedshiftBenchmarkReferenceTask(RenderingTask):
    def __init__(self, params: ExecutionParameters):
        super().__init__(params)
        date_name = date_time_with_prefix("")
        self.results_folder_name = self.params.program
        self.results_path = self.params.root_path / 'references' / self.results_folder_name
        self.result_suffix = "reference"
        self.init_folders()
        self.run_msg = "\tGenerating {color}{index}{reset}/{color}{count}{reset} [{scene}]"
        self.end_msg = '{magenta}Generation completed{reset}:\n' \
            '\tSucces: {green}{success}{reset}/{count}\n' \
            '\tFailed: {red}{errors}{reset}/{count}\n' \
            '\tSkipped:{yellow}{skipped}{reset}/{count}'
        self.results_json_log = self.results_path / f'{self.params.program}_REFERENCE_{date_name}.json'
    
    def prepare_command_line_params(self, scene: Scene) -> list:
        gpus =  split_to_gpus(self.params.gpu[0])
        cmd_params = [self.params.get_executable(), scene.path] + gpus
        return cmd_params


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

