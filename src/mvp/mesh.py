import socket

def get_bound_socket(host="", start=8500, end=8999):
    """Намертво привязывает сокет и возвращает пару (socket, port)"""
    for port in range(start, end + 1):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR спасает от зависших портов TIME_WAIT
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            # МЫ НЕ ЗАКРЫВАЕМ СОКЕТ. Возвращаем его открытым!
            return s, port
        except OSError:
            s.close()
            continue
    raise RuntimeError(f"No free ports available in range {start}-{end}")

def find_free_port(start=8500, end=8999):
    """Обертка для совместимости. Не защищает от TOCTOU гонок!"""
    s, port = get_bound_socket(start=start, end=end)
    s.close()
    return port
