import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:speech_to_text/speech_to_text.dart';
import 'package:calories_tracker/core/theme/app_colors.dart';
import 'package:calories_tracker/core/theme/app_spacing.dart';
import 'package:calories_tracker/features/auth/data/auth_session_service.dart';
import 'package:calories_tracker/features/food_log/data/food_log_api_client.dart';
import 'package:calories_tracker/features/food_log/presentation/food_diary_screen.dart';
import 'package:calories_tracker/features/food_log/presentation/food_review_screen.dart';

/// Step 1 of food logging: the user gives a photo, text or voice.
/// Voice uses the phone's own speech recognition (speech_to_text package):
/// free, no API key, supports Indian English and Hindi. The words land in the
/// text box so the user can check them before sending.
class FoodLogScreen extends StatefulWidget {
  const FoodLogScreen({super.key});

  @override
  State<FoodLogScreen> createState() => _FoodLogScreenState();
}

class _FoodLogScreenState extends State<FoodLogScreen> {
  final FoodLogApiClient _api = FoodLogApiClient();
  final AuthSessionService _authSessionService = AuthSessionService();
  final ImagePicker _imagePicker = ImagePicker();
  final SpeechToText _speech = SpeechToText();
  final TextEditingController _textController = TextEditingController();

  Uint8List? _imageBytes;
  String? _mealType;
  bool _busy = false;
  bool _listening = false;
  String? _error;

  static const _mealTypes = ['breakfast', 'lunch', 'snack', 'dinner'];

  @override
  void dispose() {
    _speech.stop();
    _textController.dispose();
    super.dispose();
  }

  Future<void> _pickImage(ImageSource source) async {
    // 1280 px / quality 85 keeps uploads around 150-300 KB.
    final file = await _imagePicker.pickImage(
      source: source,
      maxWidth: 1280,
      maxHeight: 1280,
      imageQuality: 85,
    );
    if (file == null) return;
    final bytes = await file.readAsBytes();
    if (!mounted) return;
    setState(() => _imageBytes = bytes);
  }

  Future<void> _toggleListening() async {
    if (_listening) {
      await _speech.stop();
      setState(() => _listening = false);
      return;
    }
    final available = await _speech.initialize(
      onStatus: (status) {
        if (status == 'done' || status == 'notListening') {
          if (mounted) setState(() => _listening = false);
        }
      },
      onError: (_) {
        if (mounted) setState(() => _listening = false);
      },
    );
    if (!available) {
      setState(() => _error = 'Speech recognition is not available on this phone.');
      return;
    }
    final before = _textController.text.trim();
    setState(() {
      _listening = true;
      _error = null;
    });
    await _speech.listen(
      localeId: 'en_IN',
      onResult: (result) {
        final words = result.recognizedWords;
        _textController.text = before.isEmpty ? words : '$before $words';
      },
    );
  }

  Future<void> _analyze() async {
    final text = _textController.text.trim();
    if (text.isEmpty && _imageBytes == null) {
      setState(() => _error = 'Add a photo, type the meal or tap the mic.');
      return;
    }
    final phone = await _authSessionService.getSessionPhoneNumber();
    final token = await _authSessionService.getSessionAccessToken();
    if (phone == null || token == null) {
      setState(() => _error = 'Session expired. Please login again.');
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final analysis = await _api.analyze(
        phoneNumber: phone,
        accessToken: token,
        text: text,
        imageBase64: _imageBytes == null ? null : base64Encode(_imageBytes!),
        mealType: _mealType,
      );
      if (!mounted) return;
      if (analysis.notFood || analysis.analysisId == null) {
        setState(() => _error = analysis.message.isNotEmpty
            ? analysis.message
            : 'No food found. Try a clearer photo.');
        return;
      }
      final saved = await Navigator.of(context).push<bool>(
        MaterialPageRoute(
          builder: (_) => FoodReviewScreen(
            analysis: analysis,
            phoneNumber: phone,
            accessToken: token,
            mealType: _mealType,
          ),
        ),
      );
      if (saved == true && mounted) {
        _textController.clear();
        setState(() => _imageBytes = null);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Meal saved')),
        );
      }
    } on FoodLogApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: AppColors.background,
        foregroundColor: AppColors.textPrimary,
        title: const Text('Log a meal'),
        actions: [
          IconButton(
            tooltip: 'My food diary',
            icon: const Icon(Icons.menu_book_outlined),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => const FoodDiaryScreen()),
            ),
          ),
        ],
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(AppSpacing.md),
          children: [
            _ImageBox(
              bytes: _imageBytes,
              onCamera: () => _pickImage(ImageSource.camera),
              onGallery: () => _pickImage(ImageSource.gallery),
              onClear: () => setState(() => _imageBytes = null),
            ),
            const SizedBox(height: AppSpacing.md),
            TextField(
              controller: _textController,
              minLines: 2,
              maxLines: 4,
              maxLength: 1000,
              style: const TextStyle(color: AppColors.textPrimary),
              decoration: InputDecoration(
                hintText: 'e.g. 2 phulka, 1 katori dal and aloo gobi',
                hintStyle: const TextStyle(color: AppColors.textSecondary),
                filled: true,
                fillColor: AppColors.surface,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                ),
                suffixIcon: IconButton(
                  tooltip: _listening ? 'Stop' : 'Speak',
                  icon: Icon(
                    _listening ? Icons.stop_circle : Icons.mic_none,
                    color: _listening ? AppColors.warning : AppColors.accent,
                  ),
                  onPressed: _busy ? null : _toggleListening,
                ),
              ),
            ),
            Wrap(
              spacing: AppSpacing.xs,
              children: _mealTypes
                  .map(
                    (type) => ChoiceChip(
                      label: Text(type[0].toUpperCase() + type.substring(1)),
                      selected: _mealType == type,
                      onSelected: (on) =>
                          setState(() => _mealType = on ? type : null),
                    ),
                  )
                  .toList(),
            ),
            if (_error != null) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(_error!, style: const TextStyle(color: AppColors.warning)),
            ],
            const SizedBox(height: AppSpacing.lg),
            FilledButton(
              style: FilledButton.styleFrom(
                backgroundColor: AppColors.accent,
                foregroundColor: Colors.black,
                minimumSize: const Size.fromHeight(50),
              ),
              onPressed: _busy ? null : _analyze,
              child: _busy
                  ? const SizedBox(
                      width: 22,
                      height: 22,
                      child: CircularProgressIndicator(strokeWidth: 2.5),
                    )
                  : const Text('Analyze meal'),
            ),
          ],
        ),
      ),
    );
  }
}

class _ImageBox extends StatelessWidget {
  const _ImageBox({
    required this.bytes,
    required this.onCamera,
    required this.onGallery,
    required this.onClear,
  });

  final Uint8List? bytes;
  final VoidCallback onCamera;
  final VoidCallback onGallery;
  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    if (bytes != null) {
      return Stack(
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(14),
            child: Image.memory(
              bytes!,
              height: 220,
              width: double.infinity,
              fit: BoxFit.cover,
            ),
          ),
          Positioned(
            top: 8,
            right: 8,
            child: IconButton.filled(
              icon: const Icon(Icons.close),
              onPressed: onClear,
            ),
          ),
        ],
      );
    }
    return Container(
      height: 150,
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
        children: [
          TextButton.icon(
            onPressed: onCamera,
            icon: const Icon(Icons.photo_camera_outlined, color: AppColors.accent),
            label: const Text('Camera', style: TextStyle(color: AppColors.textPrimary)),
          ),
          TextButton.icon(
            onPressed: onGallery,
            icon: const Icon(Icons.photo_library_outlined, color: AppColors.accent),
            label: const Text('Gallery', style: TextStyle(color: AppColors.textPrimary)),
          ),
        ],
      ),
    );
  }
}
