import logging
import stat as lib_stat
from os import chmod, getcwd, kill, remove, stat, system
from os.path import join
from signal import Signals
from subprocess import Popen
from time import sleep
from typing import List, Tuple, Dict

from ConfigValidator.Config.Models.FactorModel import FactorModel
from ConfigValidator.Config.Models.RunnerContext import RunnerContext
from Plugins.WasmExperiments.ProcessManager import ProcessManager
from psutil import Process


class Runner(ProcessManager):

    class RunnerConfig:
        pass

    def __init__(self, config: RunnerConfig = RunnerConfig()):
        super(Runner, self).__init__()
        self._config: RunnerConfig = config

    @property
    def config(self) -> RunnerConfig:
        return self._config

    @property
    def factors(self) -> List[FactorModel]:
        raise LookupError("\"factors\" is not implemented by this object!")

    def start(self, context: RunnerContext) -> None:
        self.reset()

    def interact(self, context: RunnerContext) -> None:
        raise LookupError("\"interact\" is not implemented by this object!")


RunnerConfig = Runner.RunnerConfig


class TimedRunner(Runner):

    class TimedRunnerConfig(RunnerConfig):

        def __init__(self, project_path: str = None, 
                           binary_path: str  = None, 
                           script_path: str  = None):

            super().__init__()

            self.project_path = project_path if project_path is not None else join(getcwd(), "WasmExperiment")
            self.binary_path  = binary_path  if binary_path  is not None else join(self.project_path, "Binaries")
            self.script_path  = script_path  if script_path  is not None else join(self.binary_path, "script.sh")

            self.max_iterations = 10
            self.sleep_time = 0.1

    def __init__(self, config: TimedRunnerConfig = TimedRunnerConfig()) -> None:
        super(TimedRunner, self).__init__(config)
        self.subprocess_id: int = None
        self.time_output: str = None

    @property
    def config(self) -> TimedRunnerConfig:
        return self._config

    @property
    def has_subprocess(self) -> bool:
        return self.subprocess_id is not None

    def create_timed_process(self, command: str, output_path: str = None) -> None:

        script_path = self.config.script_path
        with open(script_path, "w") as file:
            file.write(command)
        chmod(script_path, lib_stat.S_IRWXU | lib_stat.S_IRWXG | lib_stat.S_IRWXO)

        if output_path is not None:
            time_script = f"/usr/bin/time -f 'User: %U, System: %S' -o {output_path} {script_path}"
        else:
            time_script = f"/usr/bin/time -f 'User: %U, System: %S' {script_path}"

        self.shell_execute(time_script)

        print(f"Started time process with pid {self.pid}.")

        # check if process died immediately
        self.validate_process()

        # if not, try to access children
        shell_process = Process(self.pid)
        captured_children = False
        iteration = 1
        while not captured_children:
            try:
                script_process = shell_process.children(recursive=True)[1]
                command_process = script_process.children(recursive=True)[0]
                captured_children = True
            except IndexError:
                if iteration >= self.config.max_iterations:
                    break
                else:
                    self.validate_process()
                    logging.warning(f"Could not capture children of {shell_process.pid} instantly. Retrying after {self.config.sleep_time} seconds...")
                    iteration += 1
                    sleep(self.config.sleep_time)

        if not captured_children:
            seconds = self.config.sleep_time * iteration
            raise Exception(f"Failed to capture children for process {shell_process.pid}. Stopped after {iteration} tries (~{seconds} seconds).")

        self.subprocess_id = command_process.pid  # TODO: Figure our how to deal with multi-process environments
        self.time_output = output_path

        print(f"Started target process with pid {self.subprocess_id}.\n")

    def send_signal(self, signal: Signals) -> None:
        escaped = False

        if self.has_subprocess:
            try:
                kill(self.subprocess_id, signal)
            except ProcessLookupError:
                print()
                logging.warning(f"Target process {self.subprocess_id} already terminated.")
                escaped = True

        if self.is_running:
            try:
                kill(self.pid, signal)
            except ProcessLookupError:
                if not escaped: print()
                logging.warning(f"Time process {self.pid} already terminated.")
                escaped = True

        if escaped: print()

    def reset(self) -> None:
        super(TimedRunner, self).reset()
        self.subprocess_id = None
        self.time_output = None

    def report_time(self) -> float:
        raise LookupError("\"report\" is not implemented by this object!")


TimedRunnerConfig = TimedRunner.TimedRunnerConfig


class WasmRunner(TimedRunner):

    class WasmRunnerConfig(TimedRunnerConfig):

        DEFAULT_WASMER_PATH    = "/home/pi/.wasmer/bin/wasmer"
        DEFAULT_WASM_TIME_PATH = "/home/pi/.wasmtime/bin/wasmtime"

        def __init__(self, project_path: str = None, 
                           binary_path: str  = None, 
                           script_path: str  = None,
                           algorithms: List[str] = ["binarytrees", "spectral-norm", "nbody"],
                           languages:  List[str] = ["rust", "javascript", "go", "c"],
                           runtime_paths: Dict[str, str] = {
                                "wasmer": DEFAULT_WASMER_PATH, 
                                "wasmtime": DEFAULT_WASM_TIME_PATH
                            },
                            parameters = {
                                "binarytrees": {"input": 15, "repetitions": 18}, 
                                "spectral-norm": 6650, 
                                "nbody": 55000000
                            },
                            repretition_count: int = 10,
                            debug: bool = False):

            super().__init__(project_path, binary_path, script_path)

            self.algorithms        = algorithms
            self.languages         = languages
            self.runtime_paths     = runtime_paths
            self.parameters        = parameters
            self.repretition_count = repretition_count
            self.debug             = debug

        @property
        def runtimes(self) -> List[str]:
            return list(self.runtime_paths.keys())

        @property
        def repretitions(self) -> List[str]:
            return [str(i) for i in range(self.repretition_count)]

        def kill_runtimes(self) -> None:
            for runtime in self.runtimes:
                system(f"pkill -f {runtime}")

        def pipe_command(self, algorithm: str, language: str) -> str:
            value = self.parameters[algorithm]

            if language == "javascript":
                if algorithm == "binarytrees":
                    return "echo '{\"n\": %s, \"m\": %s}' |" % (str(value["input"]), str(value["repetitions"]))
                return "echo '{\"n\": %s}' |" % str(value)

            return ""

        def arguments(self, algorithm: str, language: str) -> str:
            if language == "javascript":
                return ""

            if algorithm == "binarytrees":
                params = self.parameters[algorithm]
                return f"{params['input']} {params['repetitions']}"

            return str(self.parameters[algorithm])

    def __init__(self, config: WasmRunnerConfig = WasmRunnerConfig()) -> None:
        super(WasmRunner, self).__init__(config)
        self.algorithms  = FactorModel("algorithm", self.config.algorithms)
        self.languages   = FactorModel("language",  self.config.languages)
        self.runtimes    = FactorModel("runtime",   self.config.runtimes)
        self.repetitions = FactorModel("id",        self.config.repretitions)

    @property
    def config(self) -> WasmRunnerConfig:
        return self._config

    @property
    def factors(self) -> List[FactorModel]:
        return [self.algorithms, self.languages, self.runtimes, self.repetitions]

    def kill_runtimes(self):
        self.config.kill_runtimes()

    def start(self, context: RunnerContext) -> Tuple[Popen, int]:
        super(WasmRunner, self).start(context)

        output_time_path = join(context.run_dir, "runtime.csv")
        run_variation = context.run_variation

        algorithm = run_variation[self.algorithms.factor_name]
        language  = run_variation[self.languages.factor_name]
        runtime   = self.config.runtime_paths[run_variation[self.runtimes.factor_name]]
        run_id    = run_variation[self.repetitions.factor_name]

        executable = join(self.config.binary_path, f"{algorithm}.{language}.wasm")
        pipe_command = self.config.pipe_command(algorithm, language)
        arguments = self.config.arguments(algorithm, language)
        command = f"{pipe_command} {runtime} {executable} {arguments}".strip()

        print("\n---------- Run Configuration ----------")
        print(f"Algorithm: {algorithm}\nLanguage: {language}\nRuntime: {run_variation[self.runtimes.factor_name]}\nID: {run_id}\n")
        print(f"Command: {command}")
        print("---------------------------------------\n")

        # Not beautiful, but gets the job done...
        # There is no obvious way for this object to know that it is supposed to set the file size.
        # But as it is the only object ever touching the actual binary, this is the easiest thing to do
        executable_size = stat(executable).st_size
        context.run_variation["storage"] = executable_size

        self.create_timed_process(command, output_time_path)

        return self.process, self.subprocess_id

    def interact(self, context: RunnerContext) -> None:
        self.wait()

    def stop(self) -> None:
        super(WasmRunner, self).stop()
        remove(self.config.script_path)

    def report_time(self) -> int:

        try:
            with open(self.time_output, "r") as file:
                line = file.readlines()[0].split()

            # calculate execution time in milliseconds
            user_time = int(float(line[1].strip(",")) * 1000)
            system_time = int(float(line[3].strip(",")) * 1000)
        except:
            # error message of time in first line, thus the process failed
            raise Exception(f"The process {self.pid} exited with an error.")

        execution_time = user_time + system_time
        return execution_time


WasmRunnerConfig = WasmRunner.WasmRunnerConfig
