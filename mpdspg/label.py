import collections
import enum
import pathlib


class LabelFileType(enum.Enum):
    ALL_EXCEPT = enum.auto()
    NONE_EXCEPT = enum.auto()


_LABEL_FILE_TYPE_BY_SUFFIX = {
    ".label-all-except": LabelFileType.ALL_EXCEPT,
    ".label-none-except": LabelFileType.NONE_EXCEPT,
}


_UPDATE_DEFAULT_EXCEPTION_BY_LABEL_FILE_TYPE = {
    LabelFileType.ALL_EXCEPT: lambda s: (s.update, s.difference_update),
    LabelFileType.NONE_EXCEPT: lambda s: (s.difference_update, s.update),
}


class LabelFilenamePair(collections.namedtuple("Filenames", "all_except,none_except")):
    __slots__ = ()

    all_except: str
    none_except: str

    def __new__(cls, label_name):
        return super().__new__(cls, f".{label_name}.label-all-except", f".{label_name}.label-none-except")


class LabelFile:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.label_name = path.stem.removeprefix('.')
        self.type = _LABEL_FILE_TYPE_BY_SUFFIX[path.suffix]
        assert '*' not in self.label_name

    def __eq__(self, other):
        return self.path == other.path

    def __lt__(self, other):
        return self.path < other.path


class DictWithEmptySetIfMissing(dict):
    EMPTY_SET = frozenset()
    def __missing__(self, key):
        return self.EMPTY_SET


class NonExistingLabelError(LookupError):
    pass


class LabelScanner:
    def __init__(self, music_root, song_ids_by_path, allow_missing=False):
        self._music_root = music_root
        if isinstance(song_ids_by_path, list):
            song_ids_by_path = {x:x for x in song_ids_by_path}
        self._song_dirs = set()
        self._songs_by_path = collections.defaultdict(list)
        for p, i in song_ids_by_path.items():
            self._songs_by_path[p].append(i)
            for parent in p.parents:
                self._song_dirs.add(parent)
                self._songs_by_path[parent].append(i)
        self._song_dirs = frozenset(music_root/d for d in self._song_dirs)
        self._songs_by_path = {p: frozenset(l) for p,l in self._songs_by_path.items()}
        if allow_missing:
            self._songs_by_path = DictWithEmptySetIfMissing(self._songs_by_path)

    def _lookup(self, label_names):
        if isinstance(label_names, str):
            label_names = label_names,
        songs_by_path = self._songs_by_path
        music_root = self._music_root
        song_dirs = self._song_dirs
        results = collections.defaultdict(set)

        label_files = [LabelFile(f)
            for name in label_names
            for fname in LabelFilenamePair(name)
            for f in music_root.rglob(fname)
        ]
        label_files.sort()

        for lf in label_files:
            label_result = results[lf.label_name]
            if lf.path.parent not in song_dirs:
                continue
            apply_default, apply_exception = _UPDATE_DEFAULT_EXCEPTION_BY_LABEL_FILE_TYPE[lf.type](label_result)
            current_dir_rel = lf.path.parent.relative_to(music_root)
            apply_default(songs_by_path[current_dir_rel])
            with open(lf.path) as f:
                for line in f:
                    line = line.rstrip()
                    if line:
                        assert '/' not in line
                        apply_exception(songs_by_path[current_dir_rel / line])

        results.default_factory = None
        return results

    def label_exists(self, arg):
        results = self._lookup(arg)
        return arg in results

    def get_existing_labels(self):
        results = self._lookup('*')
        return set(results.keys())

    def get_nonempty_labels(self):
        results = self._lookup('*')
        return {l for l,s in results.items() if s}

    def get_songs_with_label(self, arg):
        results = self._lookup(arg)
        try:
            return frozenset(results[arg])
        except KeyError:
            raise NonExistingLabelError(f"No file found for label {repr(arg)}") from None
