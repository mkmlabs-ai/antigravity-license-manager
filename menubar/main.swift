import Cocoa
import Foundation

class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var statusItem: NSStatusItem!
    let menu = NSMenu()
    let configDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/agy-switcher")

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.title = "✦ AGY"
            button.font = NSFont.systemFont(ofSize: 13, weight: .semibold)
        }
        
        menu.delegate = self
        statusItem.menu = menu
        updateMenu()
    }

    func menuWillOpen(_ menu: NSMenu) {
        updateMenu()
    }

    func updateMenu() {
        menu.removeAllItems()

        let headerItem = NSMenuItem(title: "✦ Antigravity License Hub", action: nil, keyEquivalent: "")
        headerItem.isEnabled = false
        menu.addItem(headerItem)
        menu.addItem(NSMenuItem.separator())

        let profilesFile = configDir.appendingPathComponent("profiles.json")
        let activeFile = configDir.appendingPathComponent("active")

        var activeId = ""
        if let activeData = try? Data(contentsOf: activeFile),
           let str = String(data: activeData, encoding: .utf8) {
            activeId = str.trimmingCharacters(in: .whitespacesAndNewlines)
        }

        var profiles: [[String: Any]] = []
        if let data = try? Data(contentsOf: profilesFile),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: [String: Any]] {
            for (pid, pdata) in json {
                var p = pdata
                p["id"] = pid
                profiles.append(p)
            }
        }

        profiles.sort {
            let email1 = ($0["email"] as? String) ?? ""
            let email2 = ($1["email"] as? String) ?? ""
            return email1 < email2
        }

        if profiles.isEmpty {
            let emptyItem = NSMenuItem(title: "Brak zarejestrowanych kont", action: nil, keyEquivalent: "")
            emptyItem.isEnabled = false
            menu.addItem(emptyItem)
        } else {
            for p in profiles {
                let pid = (p["id"] as? String) ?? ""
                let email = (p["email"] as? String) ?? pid
                let isAct = (pid == activeId)
                let liveQ = p["live_quota"] as? [String: Any]
                let isPro = (liveQ?["has_pro_license"] as? Bool) ?? false

                var tag = isPro ? "[Pro]" : "[Starter]"
                var title = "\(tag) \(email)"
                if isAct {
                    title = "✓ \(tag) \(email) (Aktywna)"
                }

                let item = NSMenuItem(title: title, action: #selector(switchAccountAction(_:)), keyEquivalent: "")
                item.target = self
                item.representedObject = pid
                if isAct {
                    item.state = .on
                }
                menu.addItem(item)
            }
        }

        menu.addItem(NSMenuItem.separator())

        let refreshItem = NSMenuItem(title: "↻ Odśwież statusy z Google", action: #selector(refreshAction), keyEquivalent: "r")
        refreshItem.target = self
        menu.addItem(refreshItem)

        let openWebItem = NSMenuItem(title: "🌐 Otwórz Panel Web (Dashboard)", action: #selector(openWebAction), keyEquivalent: "o")
        openWebItem.target = self
        menu.addItem(openWebItem)

        let addItem = NSMenuItem(title: "+ Dodaj nowe konto Google", action: #selector(addAccountAction), keyEquivalent: "n")
        addItem.target = self
        menu.addItem(addItem)

        menu.addItem(NSMenuItem.separator())

        let quitItem = NSMenuItem(title: "Zakończ MenuBar", action: #selector(quitAction), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)
    }

    @objc func switchAccountAction(_ sender: NSMenuItem) {
        guard let pid = sender.representedObject as? String else { return }
        let script = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/agy-switch").path
        let task = Process()
        task.launchPath = script
        task.arguments = [pid]
        task.launch()
        task.waitUntilExit()
        updateMenu()
    }

    @objc func refreshAction() {
        let script = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/agy-switch").path
        let task = Process()
        task.launchPath = script
        task.arguments = ["refresh"]
        task.launch()
    }

    @objc func openWebAction() {
        if let url = URL(string: "http://127.0.0.1:3099") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc func addAccountAction() {
        let script = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/agy-switch").path
        let task = Process()
        task.launchPath = script
        task.arguments = ["add"]
        task.launch()
    }

    @objc func quitAction() {
        NSApplication.shared.terminate(nil)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
