"""Windows Service wrapper for the HKS agent (SYSTEM privileges, auto-start,
auto-restart on failure). If the pywin32 service install ever fails, setup.bat
falls back to a SYSTEM scheduled task with the same behaviour."""
import sys
import threading

import servicemanager
import win32event
import win32service
import win32serviceutil

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))


class HKSAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "HKSAgent"
    _svc_display_name_ = "HKS Remote Agent"
    _svc_description_ = "HKS remote administration agent (C:/HKS_PROGRAM). " \
                        "Connects out to YOUR admin panel. Stop with: sc stop HKSAgent"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.alive = True

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.alive = False
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ""))
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)

    def _run(self):

        def loop():
            while self.alive:
                try:
                    from agent.main import run_agent
                    run_agent()
                except Exception:
                    import time
                    time.sleep(15)

        loop()
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STOPPED, (self._svc_name_, ""))


def handle_command_line():
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(HKSAgentService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(HKSAgentService)


if __name__ == "__main__":
    handle_command_line()
