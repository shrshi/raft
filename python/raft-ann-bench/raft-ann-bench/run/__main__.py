#
# Copyright (c) 2023, NVIDIA CORPORATION.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import json
import os
import subprocess

import yaml


def positive_int(input_str: str) -> int:
    try:
        i = int(input_str)
        if i < 1:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{input_str} is not a positive integer"
        )

    return i


def validate_algorithm(algos_conf, algo):
    algos_conf_keys = set(algos_conf.keys())
    return algo in algos_conf_keys and not algos_conf[algo]["disabled"]


def find_executable(algos_conf, algo):
    executable = algos_conf[algo]["executable"]
    conda_path = os.path.join(
        os.getenv("CONDA_PREFIX"), "bin", "ann", executable
    )
    build_path = os.path.join(
        os.getenv("RAFT_HOME"), "cpp", "build", executable
    )
    if os.path.exists(conda_path):
        print("Using RAFT bench found in conda environment: ")
        return (executable, conda_path)
    elif os.path.exists(build_path):
        print(f"Using RAFT bench from repository specified in {build_path}: ")
        return (executable, build_path)
    else:
        raise FileNotFoundError(executable)


def run_build_and_search(
    conf_filename,
    conf_file,
    executables_to_run,
    force,
    conf_filedir,
    build,
    search,
    k,
    batch_size,
):
    for executable, ann_executable_path in executables_to_run.keys():
        # Need to write temporary configuration
        temp_conf_filename = f"temporary_executable_{conf_filename}"
        temp_conf_filepath = os.path.join(conf_filedir, temp_conf_filename)
        with open(temp_conf_filepath, "w") as f:
            temp_conf = dict()
            temp_conf["dataset"] = conf_file["dataset"]
            temp_conf["search_basic_param"] = conf_file["search_basic_param"]
            temp_conf["index"] = executables_to_run[
                (executable, ann_executable_path)
            ]["index"]
            json.dump(temp_conf, f)

        if build:
            if force:
                p = subprocess.Popen(
                    [
                        ann_executable_path,
                        "--build",
                        "--overwrite",
                        temp_conf_filepath,
                    ]
                )
                p.wait()
            else:
                p = subprocess.Popen(
                    [ann_executable_path, "--build", temp_conf_filepath]
                )
                p.wait()

        if search:
            legacy_result_folder = "result/" + temp_conf["dataset"]["name"]
            os.makedirs(legacy_result_folder, exist_ok=True)
            p = subprocess.Popen(
                [
                    ann_executable_path,
                    "--search",
                    "--benchmark_counters_tabular",
                    "--benchmark_out_format=json",
                    "--override_kv=k:%s" % k,
                    "--override_kv=n_queries:%s" % batch_size,
                    f"--benchmark_out={legacy_result_folder}/{executable}.json",  # noqa: E501
                    temp_conf_filepath,
                ]
            )
            p.wait()

        os.remove(temp_conf_filepath)


def main():
    scripts_path = os.path.dirname(os.path.realpath(__file__))
    call_path = os.getcwd()
    # Read list of allowed algorithms
    try:
        import pylibraft  # noqa: F401

        algo_file = "algos.yaml"
    except ImportError:
        algo_file = "algos_cpu.yaml"
    with open(f"{scripts_path}/{algo_file}", "r") as f:
        algos_conf = yaml.safe_load(f)

    if "RAPIDS_DATASET_ROOT_DIR" in os.environ:
        default_dataset_path = os.getenv("RAPIDS_DATASET_ROOT_DIR")
    else:
        default_dataset_path = os.path.join(call_path, "datasets/")

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        "-k",
        "--count",
        default=10,
        type=positive_int,
        help="the number of nearest neighbors to search for",
    )
    parser.add_argument(
        "-bs",
        "--batch-size",
        default=10000,
        type=positive_int,
        help="number of query vectors to use in each query trial",
    )
    parser.add_argument(
        "--configuration",
        help="path to configuration file for a dataset",
    )
    parser.add_argument(
        "--dataset",
        help="dataset whose configuration file will be used",
        default="glove-100-inner",
    )
    parser.add_argument(
        "--dataset-path",
        help="path to dataset folder",
        default=default_dataset_path,
    )
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--search", action="store_true")
    parser.add_argument(
        "--algorithms",
        help="run only comma separated list of named \
                              algorithms",
        default=None,
    )
    parser.add_argument(
        "--indices",
        help="run only comma separated list of named indices. \
                              parameter `algorithms` is ignored",
        default=None,
    )
    parser.add_argument(
        "-f",
        "--force",
        help="re-run algorithms even if their results \
                              already exist",
        action="store_true",
    )

    args = parser.parse_args()

    # If both build and search are not provided,
    # run both
    if not args.build and not args.search:
        build = True
        search = True
    else:
        build = args.build
        search = args.search

    k = args.count
    batch_size = args.batch_size

    # Read configuration file associated to dataset
    if args.configuration:
        conf_filepath = args.configuration
    else:
        conf_filepath = os.path.join(
            scripts_path, "conf", f"{args.dataset}.json"
        )
    conf_filename = conf_filepath.split("/")[-1]
    conf_filedir = "/".join(conf_filepath.split("/")[:-1])
    dataset_name = conf_filename.replace(".json", "")
    dataset_path = os.path.realpath(
        os.path.join(args.dataset_path, dataset_name)
    )
    if not os.path.exists(conf_filepath):
        raise FileNotFoundError(conf_filename)

    with open(conf_filepath, "r") as f:
        conf_file = json.load(f)

    # Replace base, query to dataset-path
    conf_file["dataset"]["base_file"] = os.path.join(dataset_path, "base.fbin")
    conf_file["dataset"]["query_file"] = os.path.join(
        dataset_path, "query.fbin"
    )
    conf_file["dataset"]["groundtruth_neighbors_file"] = os.path.join(
        dataset_path, "groundtruth.neighbors.ibin"
    )
    # Ensure base and query files exist for dataset
    if not os.path.exists(conf_file["dataset"]["base_file"]):
        raise FileNotFoundError(conf_file["dataset"]["base_file"])
    if not os.path.exists(conf_file["dataset"]["query_file"]):
        raise FileNotFoundError(conf_file["dataset"]["query_file"])

    executables_to_run = dict()
    # At least one named index should exist in config file
    if args.indices:
        indices = set(args.indices.split(","))
        # algo associated with index should still be present in algos.yaml
        # and enabled
        for index in conf_file["index"]:
            curr_algo = index["algo"]
            if index["name"] in indices and validate_algorithm(
                algos_conf, curr_algo
            ):
                executable_path = find_executable(algos_conf, curr_algo)
                if executable_path not in executables_to_run:
                    executables_to_run[executable_path] = {"index": []}
                executables_to_run[executable_path]["index"].append(index)

    # switch to named algorithms if indices parameter is not supplied
    elif args.algorithms:
        algorithms = set(args.algorithms.split(","))
        # pick out algorithms from conf file that exist
        # and are enabled in algos.yaml
        for index in conf_file["index"]:
            curr_algo = index["algo"]
            if curr_algo in algorithms and validate_algorithm(
                algos_conf, curr_algo
            ):
                executable_path = find_executable(algos_conf, curr_algo)
                if executable_path not in executables_to_run:
                    executables_to_run[executable_path] = {"index": []}
                executables_to_run[executable_path]["index"].append(index)

    # default, try to run all available algorithms
    else:
        for index in conf_file["index"]:
            curr_algo = index["algo"]
            if validate_algorithm(algos_conf, curr_algo):
                executable_path = find_executable(algos_conf, curr_algo)
                if executable_path not in executables_to_run:
                    executables_to_run[executable_path] = {"index": []}
                executables_to_run[executable_path]["index"].append(index)

    # Replace build, search to dataset path
    for executable_path in executables_to_run:
        for pos, index in enumerate(
            executables_to_run[executable_path]["index"]
        ):
            index["file"] = os.path.join(dataset_path, "index", index["name"])
            index["search_result_file"] = os.path.join(
                dataset_path, "result", index["name"]
            )
            executables_to_run[executable_path]["index"][pos] = index

    run_build_and_search(
        conf_filename,
        conf_file,
        executables_to_run,
        args.force,
        conf_filedir,
        build,
        search,
        k,
        batch_size,
    )


if __name__ == "__main__":
    main()