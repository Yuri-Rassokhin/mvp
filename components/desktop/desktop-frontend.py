from desktop_ui import render_desktop_background
from desktop_logic import load_state, main

blocks = load_state()
print("BLOCKS TYPE:", type(blocks))
print("BLOCKS CONTENT:", blocks)

if isinstance(blocks, list) and all(isinstance(b, dict) and "id" in b for b in blocks):
    render_desktop_background([b["id"] for b in blocks])
else:
    print("❌ Invalid format in gui-state.json")
    render_desktop_background([])  # Показываем пустой фон без ошибок

#render_desktop_background([b["id"] for b in blocks])
main()
