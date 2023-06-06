import shutil
from datetime import datetime
from abc import ABC, abstractmethod
from bs4 import BeautifulSoup
import re
from testrunner.utils import Path

from .utils import *

USE_MULTIPROCESSING_ANALYSIS = True
try:
    from multiprocessing import Pool, Process
except ImportError:
    print('multiprocessing module was not found. Will use single threaded version')
    USE_MULTIPROCESSING_ANALYSIS = False



class AnalysisItem(ABC):
    
    reference: Path
    result: Path
    name: str
    record = dict()

    def __init__(self, reference: Path, result: Path, name: str):
        self.reference = reference
        self.result = result
        self.name = name
    
    @abstractmethod
    def analyze(self):
        pass    


class BenchmarkAnalysisItem(AnalysisItem):

    def __init__(self, reference: Path, result: Path, name: str):
        super().__init__(reference, result, name)

    def analyze(self):
        reference_data = read_html(self.reference)
        reference_time, reference_gpus = self.get_information(reference_data)
        result_data = read_html(self.result)
        result_time, result_gpus = self.get_information(result_data)

        self.record = {
            'Name': self.name,
            'Result Time[s]': result_time,
            'Result GPU(s)': result_gpus.split(","),
            'Reference Time[s]': reference_time,
            'Reference GPU(s)': reference_gpus.split(",")
        }
        
    def get_information(self, data):
        time_pattern = r"Time:\s(\d{2}h:\d{2}m:\d{2}s)"
        gpus_pattern = r"Rendering with:\s\[(.*?)\]"
        
        rendering_time = ""
        gpus_used = ""
        
        rendering_time_match = re.search(time_pattern, data)
        if rendering_time_match:
            rendering_time = rendering_time_match.group(1)
        
        gpu_mach = re.search(gpus_pattern, data)
        if gpu_mach:
            gpus_used = gpu_mach.group(1)

        if rendering_time:
            return convert_to_seconds(rendering_time), gpus_used  
        return "", ""


class CmdLineAnalysisItem(AnalysisItem):

    def __init__(self, reference: Path, result: Path, name: str):
        super().__init__(reference, result, name)
    
    def analyze(self):
        reference_data =  read_html(self.reference)
        reference_gpu_time, reference_total_time, reference_gpu_count, reference_gpu_names = self.get_information(reference_data)

        result_data = read_html(self.result)
        result_gpu_time, result_total_time, result_gpu_count, result_gpu_names = self.get_information(result_data)

        self.record = {
            "Name": self.name,
            "Result GPU Time[s]": result_gpu_time,
            "Result Total Time[s]": result_total_time,
            "Result N GPU(s)": result_gpu_count,
            "Result GPU Names": result_gpu_names,
            "Reference GPU Time[s]": reference_gpu_time,
            "Reference Total Time[s]": reference_total_time,
            "Reference N GPU(s)": reference_gpu_count,
            "Reference GPU Names": reference_gpu_names
        }
        
    
    def get_information(self, data):
        soup = BeautifulSoup(data, 'html.parser')
        
        gpu_time = ""
        gpu_info = ""
        gpu_names = []
        total_time = ""
        
        debug_lines = soup.find_all('div', class_='DEBUG line')
        pattern = r'blocks: (\d+(\.\d+)?\s?[a-zA-Z]+)'
        for line in debug_lines:
            match = re.search(pattern, line.text)
            if match:
                gpu_time = match.group(1)
        
        info_lines = soup.find_all('div', class_='INFO line')
        for line in info_lines:
            if line.b:
                if line.b.string.startswith("Rendering time:"):
                    total_time = line.b.string.split(':')[1].strip().split("(")[0].strip()
                    gpu_info = line.b.string.split('(')[1].strip(')').split()[0]

        detailed_lines = soup.find_all("div", class_ = "DETAILED line")
        pattern = r'Device \d/\d : (.*)'
        for  line in detailed_lines:
            match = re.search(pattern, line.text)
            if match:
                gpu_names.append(match.group(1))
        return gpu_time, total_time, gpu_info, gpu_names
        

class PerformanceAnalyzer:

    reference_path: Path
    results_path: Path
    analysis_type: str

    def __init__(self, reference_path: Path, results_path: Path):
        self.reference_path = reference_path
        self.results_path = results_path

        # redshiftBenchmark or redshiftCmdLine
        self.analysis_type = self.reference_path.name
        if self.analysis_type not in ['redshiftCmdLine', 'redshiftBenchmark']:
            print_error(f'Reference folder is not recognized as redshiftBaenchmark or redshiftCmdLine')
            exit(EXIT_FAILURE)

        validate_path(self.reference_path)
        validate_path(self.results_path)
    
    def analyze(self):
        items, missing_items = self.match_results_with_references()
        items = [self.get_analysis_item(item) for item in items]
        for item in items:
            item.analyze()
        records = [item.record for item in items]
        return records        
            

    def get_analysis_item(self, item)->AnalysisItem:
        if self.analysis_type == 'redshiftCmdLine':
            return CmdLineAnalysisItem(item['reference'], item['result'], item['name'])
        else:
            return BenchmarkAnalysisItem(item['reference'], item['result'], item['name'])

    def match_results_with_references(self):
        missing_items = []
        results_logs = self.results_path / 'logs'
        result_items = [Path(f) for f in results_logs.glob("*.html")]

        items = []
        for result in result_items:
            file = result.name
            name = file.split(".")[0]
            reference_file = self.reference_path / 'logs' / f'{name}.reference.html'
            if not reference_file.exists():
                missing_items.append((name, reference_file))
                print_msg(f'The reference log {reference_file} is missing')
                continue
            #print(result)
            items.append({'name': name, 'reference': reference_file, 'result': result})
        
        return items, missing_items


        




