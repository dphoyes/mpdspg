import os
import argparse
import pathlib
import mpd
import functools
from mpdspg.label import LabelScanner, LabelFilenamePair, NonExistingLabelError


def main():
    Main().main()


class Main:
    def main(self):
        parser = self.arg_parser()
        self.args = args = parser.parse_args()
        try:
            func = args.__dict__.pop('func')
        except KeyError:
            parser.print_help()
            raise SystemExit(1)
        try:
            func()
        except InvalidCmdError:
            exit(1)

    def arg_parser(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--mpd", default="/run/mpd/socket")
        subparsers = parser.add_subparsers()

        sp = subparsers.add_parser('list-labels')
        sp.add_argument("path", nargs='?')
        sp.set_defaults(func=lambda: self.Cmd().list_labels(self.args.path))

        sp = subparsers.add_parser('list-songs')
        sp.add_argument("label_name")
        sp.add_argument("path", nargs='?')
        sp.set_defaults(func=lambda: self.Cmd().list_songs(self.args.label_name, self.args.path))

        sp = subparsers.add_parser('exists')
        sp.add_argument("label_name")
        sp.set_defaults(func=lambda: exit(not self.Cmd().exists(self.args.label_name)))

        sp = subparsers.add_parser('has')
        sp.add_argument("label_name")
        sp.add_argument("song_uid", nargs='?')
        sp.set_defaults(func=lambda: exit(not self.Cmd().has(self.args.label_name, self.args.song_uid)))

        sp = subparsers.add_parser('add')
        sp.add_argument("label_name")
        sp.add_argument("song_uid")
        sp.add_argument("--new", "-n", action="store_true")
        sp.set_defaults(func=lambda: self.Cmd().add(self.args.label_name, self.args.song_uid, self.args.new))

        sp = subparsers.add_parser('remove')
        sp.add_argument("label_name")
        sp.add_argument("song_uid")
        sp.set_defaults(func=lambda: self.Cmd().remove(self.args.label_name, self.args.song_uid))

        sp = subparsers.add_parser('webserver')
        sp.add_argument("path")
        sp.set_defaults(func=lambda: self.run_webserver(self.args.path))

        return parser

    def Cmd(self, log=None):
        return Cmd(mpd=self.args.mpd, print_file=log)

    def run_webserver(self, path):
        import functools
        import socket
        from aiohttp import web
        from io import StringIO

        def cmd_handler(f):
            @functools.wraps(f)
            async def wrapped(request):
                log = StringIO()
                try:
                    await f(request, self.Cmd(log))
                except InvalidCmdError as e:
                    raise web.HTTPNotFound(text=log.getvalue()) from e
                return web.Response(text=log.getvalue())
            return wrapped

        routes = web.RouteTableDef()

        @routes.get('/list-labels')
        @cmd_handler
        async def list_labels(request, cmd):
            cmd.list_labels(request.rel_url.query.get("path"))

        @routes.post('/add')
        @cmd_handler
        async def add(request, cmd):
            data = await request.post()
            cmd.add(data["label"], data["path"], new=False)

        @routes.post('/remove')
        @cmd_handler
        async def remove(request, cmd):
            data = await request.post()
            cmd.remove(data["label"], data["path"])

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        s.bind(path)
        os.chmod(path, 0o660)

        app = web.Application()
        app.add_routes(routes)
        web.run_app(app, sock=s)


class InvalidCmdError(Exception):
    pass


class Cmd:
    def __init__(self, mpd, print_file=None):
        self._mpd = mpd
        self._print_file = print_file

    def print(self, *args, **kwargs):
        print(*args, **kwargs, file=self._print_file)

    @functools.cached_property
    def music_root(self):
        m = mpd.MPDClient()
        m.connect(self._mpd)
        return pathlib.Path(m.config()).resolve()

    @functools.cached_property
    def all_songs(self):
        return self.all_songs_in(pathlib.Path('.'))

    def all_songs_in(self, in_):
        m = mpd.MPDClient()
        m.connect(self._mpd)
        in_ = self.make_path_relative(in_)
        all_ = m.listall('' if in_==pathlib.Path('.') else in_)
        return sorted(pathlib.Path(obj['file']) for obj in all_ if 'file' in obj)

    def make_path_relative(self, path):
        if path is not None:
            path = self.make_path_absolute(path).relative_to(self.music_root)
        return path

    def make_path_absolute(self, path):
        if path is not None:
            valid = True
            try:
                path = (self.music_root/path).resolve(strict=True)
            except FileNotFoundError:
                valid = False
            valid = valid and (self.music_root in path.parents)
            if not valid:
                self.print(f"Invalid song path {path!r}")
                raise InvalidCmdError
        return path

    def list_labels(self, path=None):
        if path is None:
            labels = LabelScanner(self.music_root, {}).get_existing_labels()
        else:
            labels = LabelScanner(self.music_root, self.all_songs_in(path), allow_missing=True).get_nonempty_labels()
        for s in sorted(labels):
            self.print(s)

    def list_songs(self, label, path=None):
        if path is None:
            scanner = LabelScanner(self.music_root, self.all_songs)
        else:
            scanner = LabelScanner(self.music_root, self.all_songs_in(path), allow_missing=True)
        for s in sorted(scanner.get_songs_with_label(label)):
            self.print(s)

    def exists(self, label_name):
        scanner = LabelScanner(self.music_root, {})
        return scanner.label_exists(label_name)

    def has(self, label_name, path):
        scanner = LabelScanner(self.music_root, self.all_songs_in(path), allow_missing=True)
        return bool(scanner.get_songs_with_label(label_name))

    def add(self, label_name, path, new):
        fnames = LabelFilenamePair(label_name)
        abspath = self.make_path_absolute(path)
        if abspath.is_dir():
            if not new and not self.exists(label_name):
                self.print(f"Label {label_name} doesn't exist")
                raise InvalidCmdError
            (abspath/fnames.none_except).unlink(missing_ok=True)
            (abspath/fnames.all_except).write_text("")
            self.print(f"Added label {label_name} to {repr(path)}")
        else:
            assert abspath.is_file()
            try:
                already_has_label = self.has(label_name, path)
            except NonExistingLabelError:
                if new:
                    already_has_label = False
                else:
                    self.print(f"Label {label_name} doesn't exist")
                    raise InvalidCmdError
            if already_has_label:
                self.print(f"Label {label_name} was already added to {repr(path)}")
            else:
                none_except = abspath.parent/fnames.none_except
                all_except = abspath.parent/fnames.all_except
                if all_except.is_file() and not none_except.is_file():
                    remove_line_from_file(all_except, abspath.name)
                else:
                    add_line_to_file(none_except, abspath.name)
                self.print(f"Added label {label_name} to {repr(path)}")

    def remove(self, label_name, path):
        fnames = LabelFilenamePair(label_name)
        abspath = self.make_path_absolute(path)
        if abspath.is_dir():
            if not self.exists(label_name):
                self.print(f"Label {label_name} doesn't exist")
                raise InvalidCmdError
            (abspath/fnames.all_except).unlink(missing_ok=True)
            (abspath/fnames.none_except).write_text("")
            self.print(f"Removed label {label_name} from {repr(path)}")
        else:
            assert abspath.is_file()
            try:
                already_has_not_label = not self.has(label_name, path)
            except NonExistingLabelError:
                self.print(f"Label {label_name} doesn't exist")
                raise InvalidCmdError
            if already_has_not_label:
                self.print(f"Label {label_name} was already removed from {repr(path)}")
            else:
                none_except = abspath.parent/fnames.none_except
                all_except = abspath.parent/fnames.all_except
                if none_except.is_file():
                    remove_line_from_file(none_except, abspath.name)
                else:
                    add_line_to_file(all_except, abspath.name)
                self.print(f"Removed label {label_name} from {repr(path)}")


def add_line_to_file(filepath, line):
    if not line.endswith('\n'):
        line += '\n'
    with open(filepath, "a") as f:
        f.write(line)


def remove_line_from_file(filepath, line):
    line = line.rstrip()
    with open(filepath, "r") as f:
        all_lines = f.readlines()
    len_before = len(all_lines)
    all_lines = [l for l in all_lines if l.rstrip() != line]
    assert len(all_lines) == len_before-1
    with open(filepath, "w") as f:
        f.writelines(all_lines)


if __name__ == "__main__":
    main()
