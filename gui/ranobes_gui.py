
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

def find_next_button(soup, base_url):
    btn = soup.select_one("a[rel='next']")
    if btn and btn.get("href"):
        return urljoin(base_url, btn["href"])

    for a in soup.select("a"):
        text = a.text.lower().strip()
        if text in ["next", "next chapter", "далее", "следующая", "следующая глава"]:
            return urljoin(base_url, a.get("href"))
    return None


def load_page(url):
    r = requests.get(url, headers=HEADERS)
    if r.status_code != 200:
        return None
    return BeautifulSoup(r.text, "html.parser")


def collect_chapters(start_url, log, delay=0.3, max_chapters=5000):
    visited = set()
    chapters = []
    current = start_url

    for i in range(max_chapters):
        if current in visited:
            log("Loop detected. Stopping.")
            break

        log(f"Checking: {current}")
        soup = load_page(current)
        if soup is None:
            log("Page load failed.")
            break

        visited.add(current)
        chapters.append(current)

        next_url = find_next_button(soup, current)
        if not next_url:
            log("Last chapter reached.")
            break

        current = next_url
        time.sleep(delay)

    return chapters


class App:

    def __init__(self, root):
        self.root = root
        root.title("Ranobes Chapter Finder")

        frame = ttk.Frame(root, padding=10)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Starting Chapter Link:").pack(anchor="w")

        self.url_entry = ttk.Entry(frame, width=80)
        self.url_entry.pack(fill="x", pady=5)

        self.start_btn = ttk.Button(frame, text="Scan Chapters", command=self.start_scan)
        self.start_btn.pack(pady=5)

        self.save_btn = ttk.Button(frame, text="Save Links", command=self.save_links, state="disabled")
        self.save_btn.pack(pady=5)

        self.text = tk.Text(frame, height=20)
        self.text.pack(fill="both", expand=True)

        self.links = []

    def log(self, msg):
        self.text.insert("end", msg + "\n")
        self.text.see("end")
        self.root.update()

    def scan(self):
        start_url = self.url_entry.get().strip()

        if not start_url:
            messagebox.showerror("Error", "Enter a chapter link")
            return

        self.links = collect_chapters(start_url, self.log)

        self.log(f"Total chapters found: {len(self.links)}")

        if self.links:
            self.save_btn["state"] = "normal"

    def start_scan(self):
        self.text.delete(1.0, "end")
        thread = threading.Thread(target=self.scan)
        thread.start()

    def save_links(self):
        if not self.links:
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text file", "*.txt")]
        )

        if not path:
            return

        with open(path, "w", encoding="utf-8") as f:
            for i, link in enumerate(self.links, 1):
                f.write(f"CH{i} - {link}\n")

        messagebox.showinfo("Saved", "Chapter links saved.")


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
