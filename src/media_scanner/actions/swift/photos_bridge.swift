import Photos
import Foundation
import CoreLocation

/// Minimal CLI: photos-bridge --album "Name" or photos-bridge --update-metadata
/// --album: reads UUIDs from stdin, adds matching assets to a Photos album.
/// --update-metadata: reads JSON from stdin, updates creation date and/or GPS on assets.

func requestAuth() -> PHAuthorizationStatus {
    let sema = DispatchSemaphore(value: 0)
    var authStatus: PHAuthorizationStatus = .notDetermined
    PHPhotoLibrary.requestAuthorization(for: .readWrite) { status in
        authStatus = status
        sema.signal()
    }
    sema.wait()
    return authStatus
}

func fetchAsset(uuid: String) -> PHAsset? {
    let result = PHAsset.fetchAssets(withLocalIdentifiers: [uuid], options: nil)
    if result.count > 0 { return result.firstObject }
    let suffixed = PHAsset.fetchAssets(withLocalIdentifiers: [uuid + "/L0/001"], options: nil)
    if suffixed.count > 0 { return suffixed.firstObject }
    return nil
}

// MARK: - Album subcommand

func albumCommand(albumName: String) {
    var uuids: [String] = []
    while let line = readLine(strippingNewline: true) {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        if !trimmed.isEmpty {
            uuids.append(trimmed)
        }
    }
    if uuids.isEmpty {
        fputs("No UUIDs provided on stdin.\n", stderr)
        exit(1)
    }

    let authStatus = requestAuth()
    guard authStatus == .authorized || authStatus == .limited else {
        fputs("PhotoKit authorization denied (status=\(authStatus.rawValue)).\n", stderr)
        exit(2)
    }

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

// MARK: - Update metadata subcommand

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

func updateMetadataCommand() {
    // Read JSON from stdin
    var inputData = Data()
    while let line = readLine(strippingNewline: false) {
        if let data = line.data(using: .utf8) {
            inputData.append(data)
        }
    }

    guard !inputData.isEmpty else {
        fputs("No JSON provided on stdin.\n", stderr)
        exit(1)
    }

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

    let authStatus = requestAuth()
    guard authStatus == .authorized || authStatus == .limited else {
        fputs("PhotoKit authorization denied (status=\(authStatus.rawValue)).\n", stderr)
        exit(2)
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

// MARK: - Main dispatch

func main() {
    let args = CommandLine.arguments

    if args.contains("--update-metadata") {
        updateMetadataCommand()
    } else if let albumIdx = args.firstIndex(of: "--album"), albumIdx + 1 < args.count {
        let albumName = args[albumIdx + 1]
        albumCommand(albumName: albumName)
    } else {
        fputs("Usage: photos-bridge --album <name> | --update-metadata\n", stderr)
        exit(1)
    }
}

main()
