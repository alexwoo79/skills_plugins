import AppKit
import Foundation
import Vision

// Usage: ocr_vision <image> [lang,lang]
// Prints a JSON array: [{text, confidence, x, y, w, h}] with normalized
// boxes in Vision's coordinate space (origin bottom-left).

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: ocr_vision <image> [lang,lang]\n".data(using: .utf8)!)
    exit(2)
}
let url = URL(fileURLWithPath: args[1])
guard let image = NSImage(contentsOf: url),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot decode image\n".data(using: .utf8)!)
    exit(3)
}

let langs = args.count > 2 ? args[2].split(separator: ",").map(String.init) : ["en-US", "zh-Hans"]
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = langs

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
    try handler.perform([request])
} catch {
    FileHandle.standardError.write("ocr failed: \(error)\n".data(using: .utf8)!)
    exit(4)
}

var results: [[String: Any]] = []
for observation in request.results ?? [] {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let box = observation.boundingBox
    results.append([
        "text": candidate.string,
        "confidence": candidate.confidence,
        "x": box.origin.x,
        "y": box.origin.y,
        "w": box.size.width,
        "h": box.size.height,
    ])
}
let data = try JSONSerialization.data(withJSONObject: results, options: [])
print(String(data: data, encoding: .utf8)!)
