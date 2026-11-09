import cv2 as cv
import matplotlib
import matplotlib.pyplot as plt
from skimage.metrics import mean_squared_error
from skimage.metrics import structural_similarity as ssim
import shutil

from .utils import *

USE_MULTIPROCESSING_ANALYSIS = True
try:
    from multiprocessing import Pool, Process
except ImportError:
    print('multiprocessing module was not found. Will use single threaded version')
    USE_MULTIPROCESSING_ANALYSIS = False


matplotlib.use('Agg')

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
                540 : 436,
                562 : 470,
                576 : 484, 
                600 : 507,
                720 : 628,
                729 : 637,
                800: 708,
                900: 808,
                917: 814,
                1024: 932,
                1075: 983,
                1080: 988,
                1125: 1032,
                1200: 1097,
                1440: 1336,
                1500: 1408,
                1800: 1708,
                2048: 1956,
                2160: 2057,
                4500: 4408
        }
        h, w, n = imdata.shape
        try:
            return imdata[0: known_heights[h], :, :]
        except KeyError as e:
            print(f'{Fore.YELLOW}Warning:{ Style.RESET_ALL} Could not find crop size for height {h}')
            return imdata

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
        validate_path(self.reference_path)
        validate_path(self.results_path)
        validate_path(self.analysis_output_path)
        shutil.rmtree(self.analysis_output_path)
        self.analysis_output_path.mkdir(parents=True)

        self.treshold = treshold
        self.crop = crop
        self.analysis_items = []
        self.mismatch_items = []


    def analyze(self):
        program = "redshiftCmdBenchmark" if self.crop else "redshiftCmdLine"
        print(f'{Fore.MAGENTA}Analyzing results{Style.RESET_ALL} from {self.results_path} vs {program}')
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
        if USE_MULTIPROCESSING_ANALYSIS:
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
        images_directory = self.results_path / 'images'
        result_image_paths = [Path(f) for f in images_directory.glob('*.png')]
        self.to_compare_items = []
        for result_image in result_image_paths:
            _parts = result_image.name.split(".")[:-2] # selecet everythin except .result.png
            name = ".".join(_parts)
            reference_file = self.reference_path / 'images' / f'{name}.reference.png'
            if not reference_file.exists():
                warn_msg = f'{Fore.YELLOW}Warning: {Fore.GREEN}{reference_file}{Style.RESET_ALL} does not exists'
                missing_items.append(reference_file)
                print(warn_msg)
                continue
            self.analysis_items.append(AnalysisItem(reference_file, result_image, name, self.analysis_output_path, self.treshold, self.crop))

        number_of_all_items = len(result_image_paths)
        number_of_matcehd_items = len(self.analysis_items)
        return number_of_all_items, number_of_matcehd_items, missing_items

   
       
