from os.path import dirname, realpath
from pathlib import Path
from typing import Dict, Any, Optional
from os.path import join

from ConfigValidator.Config.Models.OperationType import OperationType
from ConfigValidator.Config.Models.RunTableModel import RunTableModel
from ConfigValidator.Config.Models.RunnerContext import RunnerContext
from EventManager.EventSubscriptionController import EventSubscriptionController
from EventManager.Models.RunnerEvents import RunnerEvents
from Plugins.WasmExperiments.Profiler import WasmProfiler, WasmReport
from Plugins.WasmExperiments.Runner import WasmRunner, WasmRunnerConfig
from ProgressManager.Output.OutputProcedure import OutputProcedure as Output


class Config(WasmRunnerConfig):

    DEBUG = False
    
    REPETITION_COUNT = 1      # should be 10 for the final run
    WAIT_PER_RUN_SEC = 1      # 60 seconds is the minimum we should use
    SHUFFLE          = False  # should be enabled for final run

    PROJECT_PATH = None  # "/home/experiment/experiment-runner-green-lab-2022/WasmExperiment"
    ALGORITHMS   = ["binarytrees", "spectral-norm", "nbody"]
    LANGUAGES    = ["rust", "javascript", "go", "c"]
    PARAMETERS   = {
        "binarytrees":   {"input": 15, "repetitions": 18}, 
        "spectral-norm": 6650, 
        "nbody":         55000000
    }

    def __init__(self) -> None:
        super(Config, self).__init__(project_path      = Config.PROJECT_PATH,
                                     algorithms        = Config.ALGORITHMS,
                                     languages         = Config.LANGUAGES,
                                     parameters        = Config.PARAMETERS,
                                     repretition_count = Config.REPETITION_COUNT,
                                     debug             = Config.DEBUG)

        self.wait_per_run : int  = Config.WAIT_PER_RUN_SEC * 1000
        self.shuffle      : bool = Config.SHUFFLE


config = Config()


class RunnerConfig:

    ROOT_DIR = Path(dirname(realpath(__file__)))

    # ================================ USER SPECIFIC CONFIG ================================
    """The name of the experiment."""
    name:                       str             = "wasm_benchmark"

    """The path in which Experiment Runner will create a folder with the name `self.name`, in order to store the
    results from this experiment. (Path does not need to exist - it will be created if necessary.)
    Output path defaults to the config file's path, inside the folder 'experiments'"""
    results_output_path:        Path             = Path(join(ROOT_DIR, 'experiments'))

    """Experiment operation type. Unless you manually want to initiate each run, use `OperationType.AUTO`."""
    operation_type:             OperationType   = OperationType.AUTO

    """The time Experiment Runner will wait after a run completes.
    This can be essential to accommodate for cooldown periods on some systems."""
    time_between_runs_in_ms:    int             = config.wait_per_run

    # Dynamic configurations can be one-time satisfied here before the program takes the config as-is
    # e.g. Setting some variable based on some criteria
    def __init__(self):
        """Executes immediately after program start, on config load"""

        self.runner = None
        self.profiler = None

        self.target_pid = None
        self.time_process = None

        EventSubscriptionController.subscribe_to_multiple_events([
            (RunnerEvents.BEFORE_EXPERIMENT, self.before_experiment),
            (RunnerEvents.BEFORE_RUN       , self.before_run       ),
            (RunnerEvents.START_RUN        , self.start_run        ),
            (RunnerEvents.START_MEASUREMENT, self.start_measurement),
            (RunnerEvents.INTERACT         , self.interact         ),
            (RunnerEvents.STOP_MEASUREMENT , self.stop_measurement ),
            (RunnerEvents.STOP_RUN         , self.stop_run         ),
            (RunnerEvents.POPULATE_RUN_DATA, self.populate_run_data),
            (RunnerEvents.AFTER_EXPERIMENT , self.after_experiment )
        ])

        self.run_table_model = None  # Initialized later
        Output.console_log("Custom config loaded")

    def create_run_table_model(self) -> RunTableModel:
        """Create and return the run_table model here. A run_table is a List (rows) of tuples (columns),
        representing each run performed"""

        self.runner: WasmRunner = WasmRunner(config)

        self.run_table_model = RunTableModel(
            factors            = self.runner.factors,
            exclude_variations = None,
            data_columns       = WasmReport.DATA_COLUMNS,
            shuffle            = config.SHUFFLE
        )

        return self.run_table_model

    def before_experiment(self) -> None:
        """Perform any activity required before starting the experiment here
        Invoked only once during the lifetime of the program."""

        pass

    def before_run(self) -> None:
        """Perform any activity required before starting a run.
        No context is available here as the run is not yet active (BEFORE RUN)"""

        self.runner.kill_runtimes()

    def start_run(self, context: RunnerContext) -> None:
        """Perform any activity required for starting the run here.
        For example, starting the target system to measure.
        Activities after starting the run should also be performed here."""

        self.time_process, self.target_pid = self.runner.start(context)

    def start_measurement(self, context: RunnerContext) -> None:
        """Perform any activity required for starting measurements."""

        self.profiler = WasmProfiler(self.target_pid, context)
        self.profiler.start()

    def interact(self, context: RunnerContext) -> None:
        """Perform any interaction with the running target system here, or block here until the target finishes."""

        self.runner.interact(context)

    def stop_measurement(self, context: RunnerContext) -> None:
        """Perform any activity here required for stopping measurements."""

        self.profiler.stop()

    def stop_run(self, context: RunnerContext) -> None:
        """Perform any activity here required for stopping the run.
        Activities after stopping the run should also be performed here."""

        self.runner.stop()
    
    def populate_run_data(self, context: RunnerContext) -> Optional[Dict[str, Any]]:
        """Parse and process any measurement data here.
        You can also store the raw measurement data under `context.run_dir`
        Returns a dictionary with keys `self.run_table_model.data_columns` and their values populated"""

        report = self.profiler.report()
        run_data = report.populate()

        run_data["execution_time"] = self.runner.report_time()

        return run_data

    def after_experiment(self) -> None:
        """Perform any activity required after stopping the experiment here
        Invoked only once during the lifetime of the program."""

        pass

    # ================================ DO NOT ALTER BELOW THIS LINE ================================
    experiment_path:            Path             = None
