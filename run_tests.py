import platform as platform
from datetime import datetime
import pandas as pd
import colorama as color_terminal

from testrunner.image_analysis import *
from testrunner.performance_analysis import *
from testrunner.render_tasks import *
from testrunner.utils import *

PYDEVD_DISABLE_FILE_VALIDATION=1



def performance_analysis(execution_parameters: ExecutionParameters) -> None:
    print("Execution performance analysis task")
    references_path = execution_parameters.root_path  / 'references' / execution_parameters.program
    results_path = execution_parameters.root_path / execution_parameters.analysis_path
    
    p = PerformanceAnalyzer(references_path, results_path)
    records = p.analyze()
    df = pd.DataFrame(records)

    analysis_output_path = results_path / 'common'
    csv_file = analysis_output_path / date_time_with_prefix("performance", "csv")
    xls_file = analysis_output_path / date_time_with_prefix("performance", "xlsx")

    df.to_csv(csv_file)
    df.to_excel(xls_file, sheet_name='Sheet1', index=False)
    

def image_analysis(execution_parameters: ExecutionParameters) -> None:
    crop = execution_parameters.program == "redshiftBenchmark"
    references_path = execution_parameters.root_path  / 'references' / execution_parameters.program
    results_path = execution_parameters.root_path / execution_parameters.analysis_path
 
    if not references_path.exists():
        print_error(f"{references_path} does not exists")
        exit(EXIT_FAILURE)
    if not results_path.exists():
        print_error(f"{results_path} does not exists")
        exit(EXIT_FAILURE)

    analyzer = ImageAnalyzer(references_path, results_path, execution_parameters.treshold, crop)
    analyzer.analyze()
    analysis_log = date_time_with_prefix("custom_analysis")
    mismatch_log = date_time_with_prefix("custom_analysis_mismach")
    analyzer.save(results_path / analysis_log)
    analyzer.save_mismatch(results_path / mismatch_log)


def execute_render_task(execution_parameters: ExecutionParameters) -> Task:
    factory = TaskFactory()
    task = factory.create_task(execution_parameters)
    task.execute()
    return task


def analyze_task_image_results(task: Task) -> None:
    crop = task.params.program == "redshiftBenchmark"
    analyzer = ImageAnalyzer(task.reference_path / task.params.program, task.results_path, task.params.treshold, crop)
    analyzer.analyze()
    analysis_log = date_time_with_prefix(f'{task.params.program}_ANALYSIS')
    mismatch_log = date_time_with_prefix(f'{task.params.program}_ANALYSIS_MISMACH')
    analyzer.save(task.results_path / analysis_log)
    analyzer.save_mismatch(task.results_path / mismatch_log)


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

    if execution_parameters.performance_analysis:
        performance_analysis(execution_parameters)
    elif execution_parameters.image_analysis:
        image_analysis(execution_parameters)
    else:
        task = execute_render_task(execution_parameters)
        # schedule results analysis for the task that was not a reference generation
        if not task.params.reference:
            analyze_task_image_results(task)
    print(f"\n{Fore.BLUE}Redshift Unit Tests Finished{Style.RESET_ALL}")
