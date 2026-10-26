# Nyabase shell prompt, sourced at the end of ~/.bashrc:
#
#   root@web-01 [aya-1] ~/project#
#
# The instance itself is named nyc-<id>; the container and server names the
# user knows come from the instance's user.nyabase.display.* labels, read
# over the Incus guest API on every new interactive shell, so a renamed
# server shows up at the next login. Without them the prompt is left alone.
#
# Colors are mid-tone macaron shades that keep ~3:1 contrast on both light
# and dark terminal backgrounds: 24-bit when the terminal says so (COLORTERM),
# the nearest xterm-256 shades otherwise, none for NO_COLOR or dumb terminals.

[[ $- == *i* ]] || return 0

__nyabase_label() {
  local sock="${NYABASE_INCUS_SOCK:-/dev/incus/sock}" value
  [[ -S $sock ]] || return 1
  value=$(curl -fsS --max-time 1 --unix-socket "$sock" \
    "http://custom.socket/1.0/config/user.nyabase.display.$1" 2>/dev/null) || return 1
  # The platform only stores visible characters; drop anything else regardless.
  value=${value//[[:cntrl:]]/}
  [[ -n $value ]] || return 1
  printf '%s' "$value"
}

__nyabase_prompt_setup() {
  local container server
  command -v curl >/dev/null 2>&1 || return 0
  container=$(__nyabase_label container) || return 0
  server=$(__nyabase_label server) || server=
  export NYABASE_CONTAINER="$container" NYABASE_SERVER="$server"

  local mode=none
  if [[ -z ${NO_COLOR:-} && ${TERM:-dumb} != dumb ]]; then
    case "${COLORTERM:-}" in
      truecolor|24bit) mode=24bit ;;
      *)
        if [[ $TERM == *256color* ]] || (( $(tput colors 2>/dev/null || echo 0) >= 256 )); then
          mode=256
        fi
        ;;
    esac
  fi

  # name: 24-bit "r;g;b" and xterm-256 fallback
  local -A rgb=(
    [rose]='214;88;138' [mint]='46;154;116' [peach]='200;112;58'
    [lavender]='141;112;208' [sky]='62;135;198' [muted]='133;133;157' [coral]='217;83;79'
  )
  local -A xterm=(
    [rose]=168 [mint]=29 [peach]=137 [lavender]=98 [sky]=67 [muted]=102 [coral]=167
  )
  # esc: raw SGR sequences; c: the same wrapped as non-printing for PS1.
  local -A esc=() c=()
  local name reset='' bold=''
  for name in "${!rgb[@]}"; do
    case $mode in
      24bit) esc[$name]=$'\e'"[38;2;${rgb[$name]}m" ;;
      256) esc[$name]=$'\e'"[38;5;${xterm[$name]}m" ;;
      *) esc[$name]='' ;;
    esac
    c[$name]=${esc[$name]:+"\[${esc[$name]}\]"}
  done
  if [[ $mode != none ]]; then
    reset='\[\e[0m\]'
    bold='\[\e[1m\]'
    # The symbol turns bold coral after a failed command; PS1 reads it on every prompt.
    __nyabase_ok=$'\e[0m'"${esc[muted]}"
    __nyabase_failed=$'\e[1m'"${esc[coral]}"
    __nyabase_symbol=$__nyabase_ok
    [[ ${PROMPT_COMMAND:-} == *__nyabase_status* ]] \
      || PROMPT_COMMAND="__nyabase_status${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
  fi

  local user_color=${c[mint]}
  (( EUID == 0 )) && user_color=${c[rose]}

  # Names enter PS1 only as ${NYABASE_*} references: bash does not re-expand
  # a variable's value, so nothing in a name can run as a command.
  local ps1="${bold}${user_color}\u${reset}${c[muted]}@${reset}${bold}${c[peach]}\${NYABASE_CONTAINER}${reset}"
  if [[ -n $server ]]; then
    ps1+=" ${c[muted]}[${reset}${c[lavender]}\${NYABASE_SERVER}${reset}${c[muted]}]${reset}"
  fi
  ps1+=" ${c[sky]}\w${reset}"
  if [[ $mode != none ]]; then
    ps1+="\[\${__nyabase_symbol}\]\\\$${reset} "
  else
    ps1+="\\\$ "
  fi

  local title=''
  case "${TERM:-}" in
    xterm*|rxvt*|screen*|tmux*|alacritty|foot*|wezterm)
      title="\[\e]0;\${NYABASE_CONTAINER}${server:+ · \${NYABASE_SERVER}}\a\]"
      ;;
  esac
  PS1="${title}${ps1}"
}

__nyabase_status() {
  if (( $? == 0 )); then __nyabase_symbol=$__nyabase_ok; else __nyabase_symbol=$__nyabase_failed; fi
}

__nyabase_prompt_setup
unset -f __nyabase_prompt_setup __nyabase_label
