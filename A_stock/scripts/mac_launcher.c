/* Run Python in the application's own process so Dock keeps one identity. */
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

#include "launcher_config.h"

static int failure(const char *message) {
    perror(message);
    return EXIT_FAILURE;
}

int main(void) {
    umask(0077);
    if (chdir(AQUANT_PROJECT) != 0) return failure("A-Quant project directory");
    if (mkdir("runtime", 0700) != 0 && errno != EEXIST) return failure("A-Quant runtime directory");
    int log = open("runtime/desktop-app.log", O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (log < 0) return failure("A-Quant application log");
    if (dup2(log, STDOUT_FILENO) < 0 || dup2(log, STDERR_FILENO) < 0) return failure("A-Quant log redirect");
    if (log > STDERR_FILENO) close(log);
    int input = open("/dev/null", O_RDONLY);
    if (input < 0 || dup2(input, STDIN_FILENO) < 0) return failure("A-Quant input redirect");
    if (input > STDERR_FILENO) close(input);
    if (setenv("AQUANT_APP_ICON", AQUANT_ICON, 1) != 0) return failure("A-Quant icon");
    if (setenv("PYTHONUNBUFFERED", "1", 1) != 0) return failure("A-Quant logging");

    /* Keep the Mach-O executable inside A-Quant.app. Spawning or execing an
       external interpreter would give the window a separate Dock identity. */
    void *library = dlopen(AQUANT_LIBPYTHON, RTLD_NOW | RTLD_GLOBAL);
    if (library == NULL) {
        fprintf(stderr, "A-Quant Python runtime: %s\n", dlerror());
        return EXIT_FAILURE;
    }
    int (*python_main)(int, char **) = (int (*)(int, char **)) dlsym(library, "Py_BytesMain");
    if (python_main == NULL) {
        fprintf(stderr, "A-Quant Python entry point: %s\n", dlerror());
        return EXIT_FAILURE;
    }
    /* Python still uses the project venv, including for the owned mail task. */
    char *arguments[] = {AQUANT_PYTHON, AQUANT_ENTRY_MODE, AQUANT_ENTRY, NULL};
    return python_main(3, arguments);
}
