# RedshiftUnitTests
 UnitTests suite for cmdline and benchmarking with reliable result comparison. 
 Created based on Maxons Unit test package. 
 
 ### FOR INTERNAL USE ONLY


## Overview

To run the unit tests for the first time you will need to do the following:
1. create config/config.user.json
2. generate the reference images/logs 
3. run the tests
4. review the results

Subsequent runs can omit step 1 and usually step 2 if no additional tests have been added and if the code hasn't changed in a way that would result in expected differences in the output images.


## Prerequisites
You will need to install Python 3. 
Then install pip and and scikit-learn matplotlib colorama
- python -m pip install --upgrade pip
- pip3 install scikit-learn matplotlib colorama


## Creating `config.<os>.user.json`

`<os>` - can be win or linux depends on the operating system you are running the tests
Open `config.<os>.user.json` and edit the `*project_root` paths to point to the scenes folder 


## Generating the reference images and logs

From the command-line
`python run_tests.py --reference --test tests\_alltests.json`


## Running the unit tests

From the command-line run
`python run_tests.py --test tests\_alltests.json`

The result images and logs along with a copy of the reference images and logs will be found in the folder results/<YYYY-MM-DD_HHMM>/

There are additional options:
--redshiftcmdline -default
--redshiftbenchmark - use redshiftBenchmark app instead of redshiftCmdLine
--gpu select gpus to use

## Reviewing the results

When the tests are run, the script will tell you whether each test succeeded or failed.
The script will review the results using scikit-image SMI algorithm and compute RMS and SMI params to check 
whether the image was rendered correctly. 
It might give a false positive, it is always recommended to check the result images 'by-eye'
The output images are organized in such a way that you can scrub through the images using FastStone image viewer - that is the reference image, result image and diff image are named so that they appear next to each other when the files are sorted alphabetically.
If an error occurs during a test, the result image will be black with red text describing the error.



## Adding additional tests

The tests to run are defined in one or more json files that you pass using the '--test' flag to run_tests.py.
You can specify multiple test json files by passing multiple --test <filename> arguments to run_tests.py.
A test file contains a list of tests, each of which are either explicit test definitions or include directives for additional test json files.
Example test json file:
```json{
	"tests": [
		{
			"include": "sometests.json"
		},
        {
            "path_to_scene": "Scenes\\UnitTests\\TestOne.scn",
            "test_name": "TestOne"
        },
		{
            "path_to_scene": "Scenes\\UnitTests\\TestTwo.scn",
            "test_name": "TestTwo"
        },
        {
			"include": "moretests.json"
		}
    ]
}
```
This example file has 2 explicit test definitions and 2 include directives.
The files 'sometests.json' and 'moretests.json' themselves each contain a list of tests (which can be explicit test definitions or include directives).

An explicit test definition has the following parameters:
`path_to_scene` (required) is the path to the scn file relative to the project root.
`test_name` (required) is the name of the test.  This is usually just the scn file name, but in general it can be any string.  The test name is used for filenames, so it should only include valid filename characters.
`frames` (optional) lets you specifiy which frame or frames should be rendered as part of the test.  If omitted, the test will render frame 1 only.  The value for frames can be one of:
  1. `<start frame>,<end frame>`, e.g. `1,10` will render frames 1 through 10
  2. `<start frame>,<end frame>,<frame step>`, e.g. `1,10,2` will render frames 1,3,5,7,9
  3. `scene`, the frame range to render will be taken from the options in the scene file

An include directive has a single parameter:
`include` (required) is the path (relative to the current json file) of the test json file to include

The file `_alltests.json` in the `tests` directory holds all `official` unit tests.
If you inspect this file, you will notice that it `includes` other files (e.g. `dl_tests.json`).

To add a new test, you have several options.
You can simply add your new test to one of the test json files that is already being processed (directly or indirectly) by run_tests.py (e.g. add the test to _alltests.json or dltests.json).
Alternatively, you can create a test json file for your new test and either pass it as an additional test to run_tests.py (using the --test flag), or include it in a test json file that is passed directly or indirectly to run_tests.py.

It should be easy to infer the correct syntax from the existing entries.


