{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
        name = "QTests";
        buildInputs = [
                pkgs.python311
                pkgs.python311Packages.tkinter
                pkgs.python311Packages.matplotlib
                pkgs.python311Packages.numpy
                pkgs.qt5.full
                pkgs.tk
                pkgs.stdenv.cc.cc.lib
                pkgs.valkey
                pkgs.apacheHttpd

        ];
        shellHook = ''
        export LD_LIBRARY_PATH=${pkgs.stdenv.cc.cc.lib}/lib:${pkgs.tk}/lib:$LD_LIBRARY_PATH
        '';
}
