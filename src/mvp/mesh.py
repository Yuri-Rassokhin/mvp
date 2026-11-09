import socket
from .config import PORT_RANGE_END, PORT_RANGE_START, SOCKET_LISTEN_BACKLOG

def get_bound_socket(host="", start=PORT_RANGE_START, end=PORT_RANGE_END):
    """Намертво привязывает сокет и возвращает пару (socket, port)"""
    for port in range(start, end + 1):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            # ДОБАВЛЕНО: Глобальный блокиратор!
            # Переводим сокет в режим прослушивания прямо здесь.
            # Теперь ни один другой процесс не сможет забиндить этот порт.
            s.listen(SOCKET_LISTEN_BACKLOG)
            return s, port
        except OSError:
            s.close()
            continue
    raise RuntimeError(f"No free ports available in range {start}-{end}")

def find_free_port(start=PORT_RANGE_START, end=PORT_RANGE_END):
    """Обертка для совместимости. Не защищает от TOCTOU гонок!"""
    s, port = get_bound_socket(start=start, end=end)
    s.close()
    return port
