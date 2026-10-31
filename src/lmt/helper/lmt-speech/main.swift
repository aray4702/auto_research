// lmt-speech: Apple's engine's helper (design/architecture.md, "Speech engines (G4)"; design/internal/g4-stage1.md §3).
//
//   lmt-speech status --locale en-US       the status line
//   lmt-speech install --locale en-US      reserves the locale, asks macOS to install its assets, then the status line
//   lmt-speech transcribe --locale en-US   16 kHz mono float32 little-endian samples on stdin, to end of file;
//                                          the status line, then volatile and final results, then done
//
// One JSON object per line on stdout, each written as it comes. A failure is an "error" line and exit 3.
// lmt builds it with `swiftc -O main.swift`, as a top-level script, into its cache; nothing else runs it.

import AVFoundation
import Foundation
import Speech

struct Failure: Error, CustomStringConvertible {
    let description: String
}

func emit(_ object: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    FileHandle.standardOutput.write(data + Data("\n".utf8))
}

func fail(_ code: String, _ message: String) -> Never {
    emit(["type": "error", "code": code, "message": message])
    exit(3)
}

/// Seconds (or a confidence) to the ms, written as such: a Double would print 17 digits.
func ms(_ value: Double) -> NSDecimalNumber {
    NSDecimalNumber(string: String(format: "%.3f", value), locale: Locale(identifier: "en_US_POSIX"))
}

func osVersion() -> String {
    let v = ProcessInfo.processInfo.operatingSystemVersion
    return v.patchVersion == 0 ? "\(v.majorVersion).\(v.minorVersion)"
        : "\(v.majorVersion).\(v.minorVersion).\(v.patchVersion)"
}

func osBuild() -> String {
    var size = 0
    guard sysctlbyname("kern.osversion", nil, &size, nil, 0) == 0, size > 0 else { return "" }
    var buffer = [CChar](repeating: 0, count: size)
    guard sysctlbyname("kern.osversion", &buffer, &size, nil, 0) == 0 else { return "" }
    return String(cString: buffer)
}

let args = CommandLine.arguments
guard args.count == 4, ["status", "install", "transcribe"].contains(args[1]), args[2] == "--locale" else {
    fail("failed", "usage: lmt-speech status|install|transcribe --locale <locale>")
}
let verb = args[1]
let requested = Locale(identifier: args[3])
let equivalent = await SpeechTranscriber.supportedLocale(equivalentTo: requested)
let locale = equivalent ?? requested
let transcriber = SpeechTranscriber(
    locale: locale, transcriptionOptions: [], reportingOptions: [.volatileResults, .fastResults],
    attributeOptions: [.audioTimeRange, .transcriptionConfidence])

func diagnose(_ text: String) {
    FileHandle.standardError.write(Data("lmt-speech: \(text)\n".utf8))
}

func assets() async -> (supported: Bool, installed: Bool) {
    guard equivalent != nil else {
        diagnose("no supported locale is equivalent to \(requested.identifier)")
        return (false, false)
    }
    // AssetInventory.status says .supported, not .installed, until this binary has reserved the locale, though
    // the assets are on the Mac and transcribe works (#576). So installed is installedLocales' answer too.
    let status = await AssetInventory.status(forModules: [transcriber])
    let installed = await SpeechTranscriber.installedLocales.map(\.identifier)
    diagnose("AssetInventory.status for \(locale.identifier): \(status); installed locales: "
             + installed.sorted().joined(separator: " "))
    guard status != .unsupported else { return (false, false) }
    return (true, status == .installed || installed.contains(locale.identifier))
}

func statusLine(_ s: (supported: Bool, installed: Bool)) {
    emit(["type": "status", "os": osVersion(), "build": osBuild(), "locale": locale.identifier,
          "supported": s.supported, "installed": s.installed])
}

/// Converts stdin's samples to the analyzer's format and passes them on, as they arrive.
final class Feeder {
    let source = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!
    let target: AVAudioFormat
    let converter: AVAudioConverter?
    let input: AsyncStream<AnalyzerInput>.Continuation

    init(target: AVAudioFormat, input: AsyncStream<AnalyzerInput>.Continuation) throws {
        self.target = target
        self.input = input
        if target == source {
            converter = nil
        } else if let converter = AVAudioConverter(from: source, to: target) {
            self.converter = converter
        } else {
            throw Failure(description: "can't convert 16 kHz float32 audio to \(target)")
        }
    }

    func feed(_ bytes: UnsafeRawBufferPointer) {
        let frames = AVAudioFrameCount(bytes.count / 4)
        let buffer = AVAudioPCMBuffer(pcmFormat: source, frameCapacity: frames)!
        buffer.frameLength = frames
        memcpy(buffer.floatChannelData![0], bytes.baseAddress!, Int(frames) * 4)
        push(buffer, end: false)
    }

    func finish() {
        push(nil, end: true)
        input.finish()
    }

    private func push(_ buffer: AVAudioPCMBuffer?, end: Bool) {
        guard let converter else {
            if let buffer { input.yield(AnalyzerInput(buffer: buffer)) }
            return
        }
        let frames = Double(buffer?.frameLength ?? 0) * target.sampleRate / source.sampleRate
        let output = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: AVAudioFrameCount(frames) + 4096)!
        var given = false
        var error: NSError?
        _ = converter.convert(to: output, error: &error) { _, status in
            if let buffer, !given {
                given = true
                status.pointee = .haveData
                return buffer
            }
            status.pointee = end ? .endOfStream : .noDataNow
            return nil
        }
        if output.frameLength > 0 { input.yield(AnalyzerInput(buffer: output)) }
    }
}

/// Reads stdin to its end in a thread of its own, feeding whole samples as they come.
func readStdin(into feeder: Feeder) async {
    await withCheckedContinuation { (done: CheckedContinuation<Void, Never>) in
        Thread {
            var pending = [UInt8]()
            var chunk = [UInt8](repeating: 0, count: 1 << 16)
            while true {
                let got = chunk.withUnsafeMutableBytes { read(0, $0.baseAddress, $0.count) }
                if got < 0 && errno == EINTR { continue }
                if got <= 0 { break }
                pending.append(contentsOf: chunk[0..<got])
                let whole = pending.count / 4 * 4
                if whole > 0 {
                    pending.withUnsafeBytes { feeder.feed(UnsafeRawBufferPointer(rebasing: $0[0..<whole])) }
                    pending.removeFirst(whole)
                }
            }
            feeder.finish()
            done.resume()
        }.start()
    }
}

func transcribe() async throws {
    guard let target = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
        throw Failure(description: "the analyzer gives no audio format")
    }
    let (input, continuation) = AsyncStream<AnalyzerInput>.makeStream()
    let feeder = try Feeder(target: target, input: continuation)
    let analyzer = SpeechAnalyzer(modules: [transcriber])
    let results = Task {
        for try await result in transcriber.results {
            let start = ms(result.range.start.seconds)
            let end = ms(result.range.end.seconds)
            guard result.isFinal else {
                emit(["type": "volatile", "start": start, "end": end, "text": String(result.text.characters)])
                continue
            }
            var words = [[String: Any]]()
            for run in result.text.runs {
                guard let time = run.audioTimeRange else { continue }
                let text = String(result.text[run.range].characters).trimmingCharacters(in: .whitespacesAndNewlines)
                if text.isEmpty { continue }
                words.append(["text": text, "start": ms(time.start.seconds), "end": ms(time.end.seconds),
                              "confidence": ms(run.transcriptionConfidence ?? 1.0)])
            }
            emit(["type": "final", "start": start, "end": end, "words": words])
        }
    }
    try await analyzer.start(inputSequence: input)
    await readStdin(into: feeder)
    try await analyzer.finalizeAndFinishThroughEndOfInput()
    try await results.value
}

switch verb {
case "status":
    statusLine(await assets())
case "install":
    guard await assets().supported else { fail("unsupported", "Apple's recognizer doesn't support \(locale.identifier) on this Mac") }
    do {
        _ = try await AssetInventory.reserve(locale: locale)
        if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            try await request.downloadAndInstall()
        }
    } catch {
        fail("failed", "the asset installation failed: \(error)")
    }
    statusLine(await assets())
default:
    let s = await assets()
    statusLine(s)
    guard s.supported else { fail("unsupported", "Apple's recognizer doesn't support \(locale.identifier) on this Mac") }
    guard s.installed else { fail("not-installed", "the \(locale.identifier) speech assets aren't installed") }
    let auth = SFSpeechRecognizer.authorizationStatus()
    if auth == .denied || auth == .restricted {
        fail("denied", "speech recognition is turned off in System Settings, Privacy & Security")
    }
    do {
        try await transcribe()
    } catch {
        fail("failed", "\(error)")
    }
    emit(["type": "done"])
}
exit(0)
