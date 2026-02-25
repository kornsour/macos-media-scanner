import Photos
import Foundation
import CoreLocation
import AppKit

/// Minimal CLI packaged as a .app bundle so macOS will display the Photos
/// permission prompt (plain CLI tools are silently denied on macOS 14+).
///
/// photos-bridge --album "Name"            (reads UUIDs from stdin or --stdin-file)
/// photos-bridge --update-metadata         (reads JSON from stdin or --stdin-file)
///
/// File-based I/O flags (used when launched via `open`):
///   --stdin-file  /path    read input from file instead of stdin
///   --stdout-file /path    write stdout to file
///   --stderr-file /path    write stderr to file

// MARK: - I/O helpers

/// Redirect stdout/stderr to files when --stdout-file / --stderr-file are given.
/// This is needed because `open` does not capture stdout/stderr.
func setupFileIO() {
    let args = CommandLine.arguments
    if let idx = args.firstIndex(of: "--stdout-file"), idx + 1 < args.count {
        let path = args[idx + 1]
        FileManager.default.createFile(atPath: path, contents: nil)
        if let fh = FileHandle(forWritingAtPath: path) {
            dup2(fh.fileDescriptor, STDOUT_FILENO)
        }
    }
    if let idx = args.firstIndex(of: "--stderr-file"), idx + 1 < args.count {
        let path = args[idx + 1]
        FileManager.default.createFile(atPath: path, contents: nil)
        if let fh = FileHandle(forWritingAtPath: path) {
            dup2(fh.fileDescriptor, STDERR_FILENO)
        }
    }
}

/// Read all input — either from a file (--stdin-file) or from stdin.
func readAllInput() -> Data {
    let args = CommandLine.arguments
    if let idx = args.firstIndex(of: "--stdin-file"), idx + 1 < args.count {
        let path = args[idx + 1]
        guard let data = FileManager.default.contents(atPath: path) else {
            fputs("Cannot read file: \(path)\n", stderr)
            exit(1)
        }
        return data
    }
    // Fall back to stdin
    var data = Data()
    while let line = readLine(strippingNewline: false) {
        if let d = line.data(using: .utf8) {
            data.append(d)
        }
    }
    return data
}

// MARK: - Shared helpers

func fetchAsset(uuid: String) -> PHAsset? {
    let result = PHAsset.fetchAssets(withLocalIdentifiers: [uuid], options: nil)
    if result.count > 0 { return result.firstObject }
    let suffixed = PHAsset.fetchAssets(withLocalIdentifiers: [uuid + "/L0/001"], options: nil)
    if suffixed.count > 0 { return suffixed.firstObject }
    return nil
}

// MARK: - Album logic

func runAlbumCommand(albumName: String, uuids: [String]) {
    var fetchResult = PHAsset.fetchAssets(withLocalIdentifiers: uuids, options: nil)
    if fetchResult.count == 0 {
        let suffixed = uuids.map { $0 + "/L0/001" }
        fetchResult = PHAsset.fetchAssets(withLocalIdentifiers: suffixed, options: nil)
    }

    if fetchResult.count == 0 {
        fputs("No assets found for any of the \(uuids.count) UUIDs.\n", stderr)
        exit(3)
    }

    let albumFetch = PHAssetCollection.fetchAssetCollections(with: .album, subtype: .any, options: nil)
    var targetAlbum: PHAssetCollection? = nil
    albumFetch.enumerateObjects { collection, _, stop in
        if collection.localizedTitle == albumName {
            targetAlbum = collection
            stop.pointee = true
        }
    }

    do {
        if targetAlbum == nil {
            try PHPhotoLibrary.shared().performChangesAndWait {
                PHAssetCollectionChangeRequest.creationRequestForAssetCollection(withTitle: albumName)
            }
            let refetch = PHAssetCollection.fetchAssetCollections(with: .album, subtype: .any, options: nil)
            refetch.enumerateObjects { collection, _, stop in
                if collection.localizedTitle == albumName {
                    targetAlbum = collection
                    stop.pointee = true
                }
            }
        }

        guard let album = targetAlbum else {
            fputs("Failed to find or create album '\(albumName)'.\n", stderr)
            exit(4)
        }

        try PHPhotoLibrary.shared().performChangesAndWait {
            guard let addRequest = PHAssetCollectionChangeRequest(for: album) else { return }
            addRequest.addAssets(fetchResult)
        }

        print("\(fetchResult.count)")
    } catch {
        fputs("PhotoKit error: \(error.localizedDescription)\n", stderr)
        exit(5)
    }
}

// MARK: - Update metadata logic

struct MetadataUpdate: Decodable {
    let uuid: String
    let date: String?
    let latitude: Double?
    let longitude: Double?
}

struct UpdateResult: Encodable {
    let success_count: Int
    let error_count: Int
    let errors: [String]
}

func runUpdateMetadataCommand(inputData: Data) {
    let updates: [MetadataUpdate]
    do {
        updates = try JSONDecoder().decode([MetadataUpdate].self, from: inputData)
    } catch {
        fputs("Invalid JSON: \(error.localizedDescription)\n", stderr)
        exit(1)
    }

    if updates.isEmpty {
        let result = UpdateResult(success_count: 0, error_count: 0, errors: [])
        let jsonData = try! JSONEncoder().encode(result)
        print(String(data: jsonData, encoding: .utf8)!)
        return
    }

    let isoFormatter = ISO8601DateFormatter()
    isoFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

    let fallbackFormatter = ISO8601DateFormatter()
    fallbackFormatter.formatOptions = [.withInternetDateTime]

    var successCount = 0
    var errors: [String] = []

    for update in updates {
        guard let asset = fetchAsset(uuid: update.uuid) else {
            errors.append("\(update.uuid):Asset not found")
            continue
        }

        do {
            try PHPhotoLibrary.shared().performChangesAndWait {
                let changeRequest = PHAssetChangeRequest(for: asset)

                if let dateStr = update.date {
                    if let date = isoFormatter.date(from: dateStr) ?? fallbackFormatter.date(from: dateStr) {
                        changeRequest.creationDate = date
                    }
                }

                if let lat = update.latitude, let lon = update.longitude {
                    changeRequest.location = CLLocation(latitude: lat, longitude: lon)
                }
            }
            successCount += 1
        } catch {
            errors.append("\(update.uuid):\(error.localizedDescription)")
        }
    }

    let result = UpdateResult(
        success_count: successCount,
        error_count: errors.count,
        errors: errors
    )
    let jsonData = try! JSONEncoder().encode(result)
    print(String(data: jsonData, encoding: .utf8)!)
}

// MARK: - App delegate (drives the NSApplication run loop)

class BridgeDelegate: NSObject, NSApplicationDelegate {

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Redirect stdout/stderr to files if requested
        setupFileIO()

        // Read all input before requesting auth
        let (mode, inputData) = parseArgs()

        // Request Photos authorization — the NSApplication run loop is active
        // so macOS can display the permission prompt dialog.
        PHPhotoLibrary.requestAuthorization(for: .readWrite) { status in
            DispatchQueue.main.async {
                guard status == .authorized || status == .limited else {
                    fputs("PhotoKit authorization denied (status=\(status.rawValue)).\n", stderr)
                    exit(2)
                }
                self.dispatch(mode: mode, inputData: inputData)
            }
        }
    }

    private func parseArgs() -> (Mode, Data) {
        let args = CommandLine.arguments
        let allInput = readAllInput()

        if args.contains("--update-metadata") {
            guard !allInput.isEmpty else {
                fputs("No JSON provided on stdin.\n", stderr)
                exit(1)
            }
            return (.updateMetadata, allInput)
        }

        if let albumIdx = args.firstIndex(of: "--album"), albumIdx + 1 < args.count {
            let albumName = args[albumIdx + 1]
            let text = String(data: allInput, encoding: .utf8) ?? ""
            let uuids = text.components(separatedBy: .newlines)
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
            if uuids.isEmpty {
                fputs("No UUIDs provided.\n", stderr)
                exit(1)
            }
            let payload = try! JSONSerialization.data(
                withJSONObject: ["albumName": albumName, "uuids": uuids]
            )
            return (.album, payload)
        }

        fputs("Usage: photos-bridge --album <name> | --update-metadata\n", stderr)
        exit(1)
    }

    private func dispatch(mode: Mode, inputData: Data) {
        switch mode {
        case .album:
            let obj = try! JSONSerialization.jsonObject(with: inputData) as! [String: Any]
            let albumName = obj["albumName"] as! String
            let uuids = obj["uuids"] as! [String]
            runAlbumCommand(albumName: albumName, uuids: uuids)
        case .updateMetadata:
            runUpdateMetadataCommand(inputData: inputData)
        }
        exit(0)
    }
}

enum Mode { case album, updateMetadata }

// MARK: - Entry point

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = BridgeDelegate()
app.delegate = delegate
app.run()
