"""Authenticated loopback DDS tunnel; serial and its agent stay on the Pi."""
import os
from .tunnel import ssh_args


def main():
    args = ssh_args()
    args[1:1] = ['-N', '-L', '127.0.0.1:15150:127.0.0.1:15150']
    os.execvp(args[0], args)


if __name__ == '__main__':
    main()
