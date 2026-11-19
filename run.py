import multiprocessing

from listener.app import main

if __name__ == "__main__":
    # Frozen child processes (e.g. multiprocessing's resource_tracker) must not start a second app.
    multiprocessing.freeze_support()
    main()
