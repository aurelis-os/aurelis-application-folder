# AppImage Launcher fish completion

function __appimage_installed_apps
    appimage list --json 2>/dev/null | python3 -c "import sys, json; [print(a['managed_name']) for a in json.load(sys.stdin)]" 2>/dev/null
end

complete -c appimage -f -n "__fish_use_subcommand" -a "daemon install remove rename list info update policy origin revert versions disable enable run browse extract verify reindex doctor config status completions export import"

# Global options
complete -c appimage -l dir -d "Applications directory" -f
complete -c appimage -l json -d "Output as JSON"
complete -c appimage -l quiet -d "Suppress non-error output" -s q
complete -c appimage -l verbose -d "Increase verbosity" -s v
complete -c appimage -l no-color -d "Disable colored output"
complete -c appimage -l no-daemon -d "Force direct mode"
complete -c appimage -l version -d "Show version"
complete -c appimage -l help -d "Show help" -s h

# install options
complete -c appimage -n "__fish_seen_subcommand_from install" -l name -d "Managed name override"
complete -c appimage -n "__fish_seen_subcommand_from install" -l move -d "Move source file instead of copy"
complete -c appimage -n "__fish_seen_subcommand_from install" -l copy -d "Copy source file (default)"
complete -c appimage -n "__fish_seen_subcommand_from install" -l replace -d "Replace existing app"
complete -c appimage -n "__fish_seen_subcommand_from install" -l keep -d "Keep both versions"
complete -c appimage -n "__fish_seen_subcommand_from install" -l cancel -d "Cancel on conflict"
complete -c appimage -n "__fish_seen_subcommand_from install" -l conflict-error -d "Print conflict payload as JSON and exit 4"
complete -c appimage -n "__fish_seen_subcommand_from install" -l origin -d "Origin record as JSON or @file" -f
complete -c appimage -n "__fish_seen_subcommand_from install" -l silent -d "Suppress success notification"
complete -c appimage -n "__fish_seen_subcommand_from install" -l yes -d "Assume yes to prompts" -s y

# remove options
complete -c appimage -n "__fish_seen_subcommand_from remove" -l keep-file -d "Leave file in place"
complete -c appimage -n "__fish_seen_subcommand_from remove" -l purge -d "Delete permanently"
complete -c appimage -n "__fish_seen_subcommand_from remove" -l yes -d "Assume yes to prompts" -s y

# update options
complete -c appimage -n "__fish_seen_subcommand_from update" -l check -d "Only check for updates"
complete -c appimage -n "__fish_seen_subcommand_from update" -l all -d "Check all apps"

# policy options
complete -c appimage -n "__fish_seen_subcommand_from policy" -a "auto manual pinned"

# origin options
complete -c appimage -n "__fish_seen_subcommand_from origin" -l set -d "Set origin record" -f
complete -c appimage -n "__fish_seen_subcommand_from origin" -l clear -d "Clear origin record"

# config options
complete -c appimage -n "__fish_seen_subcommand_from config" -a "get set list"

# completions options
complete -c appimage -n "__fish_seen_subcommand_from completions" -a "bash zsh fish"

# Arguments for subcommands that take app names
complete -c appimage -n "__fish_seen_subcommand_from remove,rename,info,update,policy,origin,revert,versions,disable,enable,run,browse,extract,verify" -a "(__appimage_installed_apps)"

# browse exec-fallback
complete -c appimage -n "__fish_seen_subcommand_from browse" -l exec-fallback -d "Use --appimage-mount"

# extract destination
complete -c appimage -n "__fish_seen_subcommand_from extract" -f -d "Destination directory"

# config subcommand arguments
complete -c appimage -n "__fish_seen_subcommand_from config; __fish_seen_subcommand_from get,set" -f

# export/import file
complete -c appimage -n "__fish_seen_subcommand_from export,import" -f