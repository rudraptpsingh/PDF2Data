import AppKit
import Foundation
import Vision

guard (CommandLine.arguments.count == 3 || CommandLine.arguments.count == 4),
      let image = NSImage(contentsOfFile: CommandLine.arguments[1]) else { exit(2) }
let device = CommandLine.arguments[2]
guard device == "auto" || device == "cpu" else { exit(2) }
let languages: [String] = CommandLine.arguments.count == 4
    ? CommandLine.arguments[3].split(separator: ",").map(String.init) : []
guard languages.count <= 4,
      languages.allSatisfy({ $0.range(of: #"^[a-z]{2,3}-[A-Z]{2}$"#, options: .regularExpression) != nil }) else { exit(2) }
var rect = NSRect(origin: .zero, size: image.size)
guard let cg = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else { exit(2) }
let request = VNRecognizeTextRequest()
if device == "cpu" { request.usesCPUOnly = true }
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
if languages.isEmpty {
    request.automaticallyDetectsLanguage = true
} else {
    request.recognitionLanguages = languages
}
try VNImageRequestHandler(cgImage: cg).perform([request])
let observations = (request.results ?? []).sorted {
    abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.012
      ? $0.boundingBox.midY > $1.boundingBox.midY : $0.boundingBox.minX < $1.boundingBox.minX
}
let lines: [[String: Any]] = observations.compactMap { observation in
    guard let candidate = observation.topCandidates(1).first else { return nil }
    let box = observation.boundingBox
    let pattern = try? NSRegularExpression(pattern: "\\S+")
    let matches = pattern?.matches(in: candidate.string, range: NSRange(candidate.string.startIndex..., in: candidate.string)) ?? []
    let words: [[String: Any]] = matches.compactMap { match in
        guard let range = Range(match.range, in: candidate.string),
              let wordBox = try? candidate.boundingBox(for: range) else { return nil }
        let b = wordBox.boundingBox
        return ["text": String(candidate.string[range]),
                "bbox": [b.minX, 1 - b.maxY, b.maxX, 1 - b.minY]]
    }
    return ["text": candidate.string, "confidence": Double(candidate.confidence),
            "bbox": [box.minX, 1 - box.maxY, box.maxX, 1 - box.minY], "words": words]
}
let data = try JSONSerialization.data(withJSONObject: ["lines": lines], options: [.sortedKeys])
FileHandle.standardOutput.write(data)
