import Photos
import Foundation

/// Minimal CLI: reads UUIDs from stdin, adds matching assets to a Photos album.
/// Usage: photos-bridge --album "Album Name"

func main() {
    // Parse --album argument
    let args = CommandLine.arguments
    guard let albumIdx = args.firstIndex(of: "--album"),
          albumIdx + 1 < args.count else {
        fputs("Usage: photos-bridge --album <name>\n", stderr)
        exit(1)
    }
    let albumName = args[albumIdx + 1]

    // Read UUIDs from stdin (one per line)
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

    // Request PhotoKit authorization
    let sema = DispatchSemaphore(value: 0)
    var authStatus: PHAuthorizationStatus = .notDetermined
    PHPhotoLibrary.requestAuthorization(for: .readWrite) { status in
        authStatus = status
        sema.signal()
    }
    sema.wait()

    guard authStatus == .authorized || authStatus == .limited else {
        fputs("PhotoKit authorization denied (status=\(authStatus.rawValue)).\n", stderr)
        exit(2)
    }

    // Fetch assets by UUID — try bare UUIDs first, then with /L0/001 suffix
    var fetchResult = PHAsset.fetchAssets(withLocalIdentifiers: uuids, options: nil)
    if fetchResult.count == 0 {
        let suffixed = uuids.map { $0 + "/L0/001" }
        fetchResult = PHAsset.fetchAssets(withLocalIdentifiers: suffixed, options: nil)
    }

    if fetchResult.count == 0 {
        fputs("No assets found for any of the \(uuids.count) UUIDs.\n", stderr)
        exit(3)
    }

    // Find or create album
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
            // Re-fetch to get the created album
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

        // Add assets to album
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

main()
