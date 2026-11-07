import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:calories_tracker/core/config/app_config.dart';
import 'package:calories_tracker/core/network/app_session_auto_refresh_client.dart';
import 'package:calories_tracker/core/network/auth_session_guard.dart';

/// Talks to the food engine routes in host-backend (/api/auth/food/...).
/// Same pattern as a typical authenticated API client: Bearer token + phoneNumber in the body.
/// The app never sees the Gemini key; it only calls our backend.

class FoodLogApiException implements Exception {
  FoodLogApiException({required this.message, this.statusCode});

  final String message;
  final int? statusCode;

  @override
  String toString() => message;
}

double _num(dynamic value, [double fallback = 0]) =>
    value is num ? value.toDouble() : fallback;

Map<String, double> _nutrients(dynamic raw) {
  if (raw is! Map) return const {};
  return raw.map((key, value) => MapEntry(key.toString(), _num(value)));
}

class FoodUnitOption {
  const FoodUnitOption({
    required this.unit,
    required this.gramsPerUnit,
    required this.yours,
  });

  final String unit;
  final double gramsPerUnit;
  final bool yours;

  static FoodUnitOption fromJson(Map<String, dynamic> json) => FoodUnitOption(
    unit: (json['unit'] as String? ?? 'g').trim(),
    gramsPerUnit: _num(json['gramsPerUnit'], 1),
    yours: json['yours'] == true,
  );
}

class FoodChoice {
  const FoodChoice({
    required this.foodCode,
    required this.foodName,
    this.kcalPer100g,
    this.suspect = false,
  });

  final String foodCode;
  final String foodName;
  final double? kcalPer100g;
  final bool suspect;

  static FoodChoice fromJson(Map<String, dynamic> json) => FoodChoice(
    foodCode: (json['foodCode'] as String? ?? '').trim(),
    foodName: (json['foodName'] as String? ?? '').trim(),
    kcalPer100g: (json['kcalPer100g'] as num?)?.toDouble(),
    suspect: json['quality'] == 'suspect',
  );
}

class FoodItem {
  const FoodItem({
    required this.itemId,
    required this.name,
    required this.aiName,
    required this.quantity,
    required this.unit,
    required this.grams,
    required this.gramsSource,
    required this.source,
    required this.matchMethod,
    required this.dataSource,
    required this.nutrients,
    required this.aiEstimate,
    required this.needsReview,
    required this.reviewMessages,
    required this.note,
    required this.alternatives,
    required this.unitOptions,
    required this.edited,
  });

  final String itemId;
  final String name;
  final String aiName;
  final double quantity;
  final String unit;
  final double grams;
  final String gramsSource; // ai | your_usual | user | database_serving | default
  final String source; // database | ai_estimate | none
  final String? matchMethod;
  final String? dataSource; // e.g. "INDB 2024.11"
  final Map<String, double> nutrients;
  final Map<String, double>? aiEstimate;
  final bool needsReview;
  final List<String> reviewMessages;
  final String note;
  final List<FoodChoice> alternatives;
  final List<FoodUnitOption> unitOptions;
  final bool edited;

  bool get isVerified => source == 'database';
  double get kcal => nutrients['energy_kcal'] ?? 0;

  static FoodItem fromJson(Map<String, dynamic> json) {
    final match = json['match'] as Map<String, dynamic>?;
    final estimate = json['aiEstimate'];
    return FoodItem(
      itemId: (json['itemId'] as String? ?? '').trim(),
      name: (json['name'] as String? ?? '').trim(),
      aiName: (json['aiName'] as String? ?? '').trim(),
      quantity: _num(json['quantity'], 1),
      unit: (json['unit'] as String? ?? 'serving').trim(),
      grams: _num(json['grams']),
      gramsSource: (json['gramsSource'] as String? ?? '').trim(),
      source: (json['source'] as String? ?? 'none').trim(),
      matchMethod: match?['method'] as String?,
      dataSource: match?['dataSource'] as String?,
      nutrients: _nutrients(json['nutrients']),
      aiEstimate: estimate is Map ? _nutrients(estimate) : null,
      needsReview: json['needsReview'] == true,
      reviewMessages: (json['reviewMessages'] as List? ?? const [])
          .map((m) => m.toString())
          .toList(growable: false),
      note: (json['note'] as String? ?? '').trim(),
      alternatives: (json['alternatives'] as List? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(FoodChoice.fromJson)
          .toList(growable: false),
      unitOptions: (json['unitOptions'] as List? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(FoodUnitOption.fromJson)
          .toList(growable: false),
      edited: json['edited'] == true,
    );
  }
}

class FoodAnalysis {
  const FoodAnalysis({
    required this.analysisId,
    required this.notFood,
    required this.message,
    required this.transcript,
    required this.items,
    required this.totals,
    required this.needsReview,
    required this.databaseEnabled,
  });

  final String? analysisId;
  final bool notFood;
  final String message;
  final String transcript;
  final List<FoodItem> items;
  final Map<String, double> totals;
  final bool needsReview;

  /// False when the backend runs with FOOD_USE_DATABASE=false (AI estimates only):
  /// hide "pick from our food list" and let the user type the dish name.
  final bool databaseEnabled;

  static FoodAnalysis fromJson(Map<String, dynamic> json) => FoodAnalysis(
    analysisId: json['analysisId'] as String?,
    notFood: json['notFood'] == true,
    message: (json['message'] as String? ?? '').trim(),
    transcript: (json['transcript'] as String? ?? '').trim(),
    items: (json['items'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(FoodItem.fromJson)
        .toList(growable: false),
    totals: _nutrients(json['totals']),
    needsReview: json['needsReview'] == true,
    databaseEnabled: json['databaseEnabled'] != false,
  );
}

class FoodMeal {
  const FoodMeal({
    required this.id,
    required this.mealType,
    required this.loggedAt,
    required this.itemNames,
    required this.totals,
  });

  final String id;
  final String mealType;
  final String loggedAt;
  final List<String> itemNames;
  final Map<String, double> totals;

  static FoodMeal fromJson(Map<String, dynamic> json) => FoodMeal(
    id: (json['id'] as String? ?? '').trim(),
    mealType: (json['mealType'] as String? ?? '').trim(),
    loggedAt: (json['loggedAt'] as String? ?? '').trim(),
    itemNames: (json['items'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map((i) => (i['name'] as String? ?? '').trim())
        .toList(growable: false),
    totals: _nutrients(json['totals']),
  );
}

class FoodDiaryDay {
  const FoodDiaryDay({
    required this.date,
    required this.meals,
    required this.totals,
  });

  final String date;
  final List<FoodMeal> meals;
  final Map<String, double> totals;

  static FoodDiaryDay fromJson(Map<String, dynamic> json) => FoodDiaryDay(
    date: (json['date'] as String? ?? '').trim(),
    meals: (json['meals'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(FoodMeal.fromJson)
        .toList(growable: false),
    totals: _nutrients(json['totals']),
  );
}

class FoodLogApiClient {
  FoodLogApiClient({http.Client? client})
    : _client = client ?? AppSessionAutoRefreshClient();

  final http.Client _client;
  // AI calls (analyze, typed corrections) can take several seconds.
  static const Duration _aiTimeout = Duration(seconds: 60);
  static const Duration _requestTimeout = Duration(seconds: 20);

  Future<Map<String, dynamic>> _post(
    String path, {
    required String phoneNumber,
    required String accessToken,
    Map<String, dynamic> body = const {},
    Duration timeout = _requestTimeout,
    String fallbackError = 'Something went wrong. Please try again.',
  }) async {
    final normalizedPhone = phoneNumber.trim();
    final normalizedToken = accessToken.trim();
    if (normalizedPhone.isEmpty || normalizedToken.isEmpty) {
      throw FoodLogApiException(
        message: 'Session expired. Please login again.',
        statusCode: 401,
      );
    }

    http.Response response;
    try {
      response = await _client
          .post(
            Uri.parse('${AppConfig.backendBaseUrl}$path'),
            headers: {
              'Content-Type': 'application/json',
              'Authorization': 'Bearer $normalizedToken',
            },
            body: jsonEncode({'phoneNumber': normalizedPhone, ...body}),
          )
          .timeout(timeout);
    } on TimeoutException {
      throw FoodLogApiException(
        message: 'Request timed out. Please try again.',
      );
    } on Object {
      throw FoodLogApiException(
        message: 'Unable to connect right now. Please try again.',
      );
    }

    Map<String, dynamic> data;
    try {
      final decoded = jsonDecode(response.body);
      data = decoded is Map<String, dynamic>
          ? decoded
          : <String, dynamic>{'detail': 'Invalid response format'};
    } catch (_) {
      data = <String, dynamic>{'detail': 'Invalid response format'};
    }

    if (response.statusCode < 200 || response.statusCode >= 300) {
      final detail = data['detail'] is String
          ? (data['detail'] as String).trim()
          : null;
      if (AuthSessionGuard.shouldInvalidateSession(
        statusCode: response.statusCode,
        detail: detail,
      )) {
        AuthSessionGuard.notifySessionExpired();
      }
      throw FoodLogApiException(
        message: detail?.isNotEmpty == true ? detail! : fallbackError,
        statusCode: response.statusCode,
      );
    }
    return data;
  }

  /// Photo (base64 JPEG) and/or text. Speech is turned into text on the phone.
  Future<FoodAnalysis> analyze({
    required String phoneNumber,
    required String accessToken,
    String? text,
    String? imageBase64,
    String? mealType,
  }) async {
    final data = await _post(
      '/api/auth/food/analyze',
      phoneNumber: phoneNumber,
      accessToken: accessToken,
      timeout: _aiTimeout,
      fallbackError: 'Could not read this meal. Please try again.',
      body: {
        if (text != null && text.trim().isNotEmpty) 'text': text.trim(),
        if (imageBase64 != null) 'imageBase64': imageBase64,
        if (imageBase64 != null) 'imageMimeType': 'image/jpeg',
        if (mealType != null) 'mealType': mealType,
      },
    );
    return FoodAnalysis.fromJson(data);
  }

  /// action: replace | set_amount | remove | add | use_estimate
  Future<FoodAnalysis> fix({
    required String phoneNumber,
    required String accessToken,
    required String analysisId,
    required String action,
    String? itemId,
    String? foodCode,
    String? correctedName,
    double? grams,
    double? quantity,
    String? unit,
  }) async {
    final data = await _post(
      '/api/auth/food/fix',
      phoneNumber: phoneNumber,
      accessToken: accessToken,
      timeout: _aiTimeout,
      fallbackError: 'Could not update this item.',
      body: {
        'analysisId': analysisId,
        'action': action,
        if (itemId != null) 'itemId': itemId,
        if (foodCode != null) 'foodCode': foodCode,
        if (correctedName != null) 'correctedName': correctedName,
        if (grams != null) 'grams': grams,
        if (quantity != null) 'quantity': quantity,
        if (unit != null) 'unit': unit,
      },
    );
    return FoodAnalysis.fromJson(data);
  }

  Future<FoodMeal> confirm({
    required String phoneNumber,
    required String accessToken,
    required String analysisId,
    String? mealType,
  }) async {
    final data = await _post(
      '/api/auth/food/confirm',
      phoneNumber: phoneNumber,
      accessToken: accessToken,
      fallbackError: 'Could not save this meal.',
      body: {
        'analysisId': analysisId,
        if (mealType != null) 'mealType': mealType,
      },
    );
    return FoodMeal.fromJson(
      (data['meal'] as Map<String, dynamic>?) ?? const {},
    );
  }

  Future<List<FoodChoice>> search({
    required String phoneNumber,
    required String accessToken,
    required String query,
  }) async {
    final data = await _post(
      '/api/auth/food/search',
      phoneNumber: phoneNumber,
      accessToken: accessToken,
      body: {'query': query.trim(), 'limit': 8},
    );
    return (data['results'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(FoodChoice.fromJson)
        .toList(growable: false);
  }

  Future<List<FoodDiaryDay>> history({
    required String phoneNumber,
    required String accessToken,
    int days = 1,
  }) async {
    final data = await _post(
      '/api/auth/food/history',
      phoneNumber: phoneNumber,
      accessToken: accessToken,
      body: {'days': days},
    );
    return (data['days'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(FoodDiaryDay.fromJson)
        .toList(growable: false);
  }

  Future<void> deleteMeal({
    required String phoneNumber,
    required String accessToken,
    required String mealId,
  }) async {
    await _post(
      '/api/auth/food/meals/delete',
      phoneNumber: phoneNumber,
      accessToken: accessToken,
      body: {'mealId': mealId},
    );
  }
}
