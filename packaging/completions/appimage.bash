# AppImage Launcher bash completion

_appimage_completions() {
    local cur prev opts base
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    opts="daemon install remove rename list info update policy origin revert versions disable enable run browse extract verify reindex doctor config status completions export import"

    # Global options
    global_opts="--dir --json --quiet --verbose --no-color --no-daemon --version --help"

    case "${prev}" in
        appimage)
            COMPREPLY=( $(compgen -W "${opts} ${global_opts}" -- ${cur}) )
            return 0
            ;;
        install)
            COMPREPLY=( $(compgen -W "--name --move --copy --replace --keep --cancel --conflict-error --origin --silent --yes" -- ${cur}) )
            return 0
            ;;
        remove)
            COMPREPLY=( $(compgen -W "--keep-file --purge --yes" -- ${cur}) )
            return 0
            ;;
        update)
            COMPREPLY=( $(compgen -W "--check --all" -- ${cur}) )
            return 0
            ;;
        policy)
            COMPREPLY=( $(compgen -W "auto manual pinned" -- ${cur}) )
            return 0
            ;;
        origin)
            COMPREPLY=( $(compgen -W "--set --clear" -- ${cur}) )
            return 0
            ;;
        config)
            COMPREPLY=( $(compgen -W "get set list" -- ${cur}) )
            return 0
            ;;
        completions)
            COMPREPLY=( $(compgen -W "bash zsh fish" -- ${cur}) )
            return 0
            ;;
        --dir)
            COMPREPLY=( $(compgen -d -- ${cur}) )
            return 0
            ;;
        --origin)
            COMPREPLY=( $(compgen -f -- ${cur}) )
            return 0
            ;;
    esac

    # Default: complete with command options or files
    if [[ ${cur} == -* ]]; then
        COMPREPLY=( $(compgen -W "${global_opts}" -- ${cur}) )
    else
        COMPREPLY=( $(compgen -f -- ${cur}) )
    fi
}

complete -F _appimage_completions appimage