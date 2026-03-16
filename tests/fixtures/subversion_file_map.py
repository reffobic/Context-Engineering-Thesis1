"""
File map for Apache Subversion (https://github.com/apache/subversion).

Used for file-level context selection on a real, multi-language codebase
(C, Python, Java, C++, etc.). Team chose this codebase for context-engineering
experiments.

Source: repo layout and README at https://github.com/apache/subversion
"""

from src.file_context.file_map import FileEntry, FileMap

# Representative file map: top-level dirs + key root files + important subversion/ paths
SUBVERSION_ENTRIES = [
    # Root / build & config
    FileEntry("README", "Project overview, pointers, quickstart, converting from CVS"),
    FileEntry("INSTALL", "Build and install instructions"),
    FileEntry("CHANGES", "Changelog for releases"),
    FileEntry("CMakeLists.txt", "CMake build configuration (primary build system)"),
    FileEntry("Makefile.in", "Autotools Makefile template"),
    FileEntry("configure.ac", "Autoconf configuration (detect deps, set build options)"),
    FileEntry("build.conf", "Build system config: targets, linkage, checksum backends"),
    FileEntry("aclocal.m4.in", "Autotools macros"),
    FileEntry("autogen.sh", "Generate configure and build scripts"),
    FileEntry("gen-make.py", "Generator for Makefile from build.conf"),
    FileEntry("get-deps.sh", "Fetch build dependencies"),
    # CI / dev config
    FileEntry(".github/workflows", "GitHub Actions CI workflows"),
    FileEntry(".asf.yaml", "ASF (Apache Software Foundation) repo config"),
    FileEntry(".clang-format", "C/C++ formatting rules"),
    FileEntry(".editorconfig", "Editor settings (indent, charset)"),
    FileEntry(".ycm_extra_conf.py", "YouCompleteMe / clang extra config for C"),
    # Top-level dirs (purpose only; subversion/ expanded below)
    FileEntry("build", "Build system scripts and generated build files"),
    FileEntry("contrib", "Contributed scripts and tools (community)"),
    FileEntry("doc", "Documentation (links to Subversion Book, etc.)"),
    FileEntry("notes", "Developer notes and knobs"),
    FileEntry("tools", "Development and test tools (e.g. win-tests.py)"),
    # subversion/ — core code (main C library + CLI)
    FileEntry("subversion", "Root of core Subversion C library and CLI source"),
    FileEntry("subversion/include", "Public API headers for libsvn_*"),
    FileEntry("subversion/libsvn_client", "Client library: checkout, update, commit, merge"),
    FileEntry("subversion/libsvn_delta", "Delta (diff/patch) and tree handling"),
    FileEntry("subversion/libsvn_fs", "Filesystem abstraction (FSFS, BDB)"),
    FileEntry("subversion/libsvn_ra", "Repository access layer (RA) and RA implementations"),
    FileEntry("subversion/libsvn_repos", "Repository creation and management"),
    FileEntry("subversion/libsvn_subr", "Shared utilities: config, crypto, checksum, streams"),
    FileEntry("subversion/libsvn_wc", "Working copy management"),
    FileEntry("subversion/svn", "Command-line client (svn) entry point and dispatch"),
    FileEntry("subversion/svnserve", "Standalone SVN server daemon"),
    FileEntry("subversion/tests", "Test suite for C library and CLI"),
    # Python bindings / scripts (repo has ~18% Python)
    FileEntry("subversion/bindings", "Language bindings (e.g. Python, Perl, Ruby)"),
    FileEntry("win-tests.py", "Windows test runner"),
    # Legal / community
    FileEntry("LICENSE", "Apache-2.0 license"),
    FileEntry("NOTICE", "Notices and attributions"),
    FileEntry("BUGS", "Bug tracking / known issues"),
    FileEntry("COMMITTERS", "Committer list and policy"),
]


def get_subversion_file_map() -> FileMap:
    return FileMap(name="Apache Subversion", entries=SUBVERSION_ENTRIES)
