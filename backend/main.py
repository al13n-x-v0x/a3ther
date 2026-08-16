# =========================================================================================
# A.3.T.H.E.R ENGINE — BACKEND
# PHASE 2.1 — MAIN CORE LAUNCHER
# Adaptive 3rd-generation Technology for Heuristic Execution & Research
# AL13N INDUSTRIES
# =========================================================================================


import os
import sys
import time
import importlib

# Configure logging early
try:
    import core.logging_config as logging_config
    importlib.reload(logging_config)
except Exception:
    pass

# Timekeeper service
try:
    import core.timekeeper as timekeeper_module
except Exception:
    timekeeper_module = None

# Ensure project root is on sys.path when running as a script
if __package__ is None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)


# ===============================
# CORE CONFIGURATION
# ===============================


AETHER_BACKEND = {

    "name": "A.3.T.H.E.R Backend",

    "version": "Phase 2.1",

    "status": "OFFLINE",

    "modules": []

}



# ===============================
# MODULE LOADER
# ===============================


def LoadModule(name):

    try:

        __import__(name)

        AETHER_BACKEND["modules"].append(name)

        print(
            f"[LOADED] {name}"
        )

        return True


    except Exception as error:

        print(
            f"[FAILED] {name}",
            error
        )

        return False





# ===============================
# DATABASE INITIALIZATION
# ===============================


def InitializeDatabase():


    try:
        # Use package-qualified import to support module and script runs
        from backend.ai.memory import InitializeMemory

        InitializeMemory()


        print(
            "[DATABASE] Memory system ready"
        )


    except Exception as error:

        print(
            "[DATABASE ERROR]",
            error
        )







# ===============================
# AI CORE INITIALIZATION
# ===============================


def InitializeAI():


    try:
        # Use package-qualified import to support module and script runs
        from backend.ai.brain import InitializeBrain

        InitializeBrain()


        print(
            "[AI] Brain online"
        )


    except Exception as error:

        print(
            "[AI ERROR]",
            error
        )







# ===============================
# API SERVER START
# ===============================


def StartAPI():


    try:

        import uvicorn


        print(
            "[API] Starting server..."
        )


        uvicorn.run(
            "backend.api.server:app",
            host="0.0.0.0",
            port=8000,
            reload=True,
        )


    except Exception as error:


        print(
            "[API ERROR]",
            error
        )








# ===============================
# AETHER BOOT SEQUENCE
# ===============================


def BootAETHER():


    print(
    """

    ╔════════════════════════════╗
    ║   A.3.T.H.E.R BACKEND       ║
    ║      BOOT SEQUENCE          ║
    ╚════════════════════════════╝

    """
    )


    time.sleep(1)



    InitializeDatabase()


    InitializeAI()

    # Start timekeeper if available
    try:
        if timekeeper_module is not None:
            tk = timekeeper_module.get_timekeeper()
            tk.start()
            print("[TIME] TimeKeeper started")
    except Exception as e:
        print("[TIME] TimeKeeper failed to start:", e)


    AETHER_BACKEND["status"] = "ONLINE"



    print(
        "[AETHER] Backend ONLINE"
    )


    StartAPI()








# ===============================
# SHUTDOWN HANDLER
# ===============================


def Shutdown():

    print(
        "[AETHER] Shutting down..."
    )


    AETHER_BACKEND["status"] = "OFFLINE"
    try:
        if timekeeper_module is not None:
            tk = timekeeper_module.get_timekeeper()
            tk.stop()
            print("[TIME] TimeKeeper stopped")
    except Exception as e:
        print("[TIME] TimeKeeper failed to stop:", e)



# ===============================
# MAIN ENTRY
# ===============================


if __name__ == "__main__":


    try:

        BootAETHER()


    except KeyboardInterrupt:


        Shutdown()


    except Exception as error:


        print(
            "[CRITICAL ERROR]",
            error
        )