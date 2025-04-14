{ pkgs }: {
  deps = [
    pkgs.python310
    pkgs.chromium
    pkgs.chromedriver
    pkgs.glib
  ];
}
